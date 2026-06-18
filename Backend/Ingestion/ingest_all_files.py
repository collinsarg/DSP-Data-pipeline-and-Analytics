#!/usr/bin/env python3
"""
Ingest Amazon DSP data files (CSV/XLSX and WST ZIP bundle) into SQL Server.

- Uses filename patterns to route each file to the correct dsp.* table(s)
- Extracts timestamp from filename ONLY for tables that require a snapshot/file time
  (Routes.snapshot_dt, Itineraries.file_datetime). Others use values present in the file.
- Handles row/column "matrix" sheets by unpivoting to (date/week, metric_name, metric_value)
- Tolerates 'Missing', '', 'N/A' text as NULLs and trims whitespace
- Designed for repeated runs; will skip fully empty batches gracefully

Requirements:
    pip install pyodbc pandas openpyxl python-dateutil

Configure connection via environment or inline below.
"""

import os, re, io, zipfile, math, json, argparse
from datetime import datetime, date, timedelta
from typing import Optional, Tuple, Iterable, Dict, Any, List
from collections import defaultdict

from numpy import long
import pandas as pd
import pyodbc
from dateutil import parser as dtparser

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Any

import os
from dotenv import load_dotenv
import numpy as np

# Load .env into environment (only once, at top-level is fine)
load_dotenv()

def _req(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required .env key: {key}")
    return val

SQL_SERVER      = _req("SQLSERVER_HOST")
SQL_PORT        = os.getenv("SQLSERVER_PORT")  # optional
SQL_DATABASE    = _req("SQLSERVER_DATABASE")
SQL_USER        = _req("SQLSERVER_USER")
SQL_PASSWORD    = _req("SQLSERVER_PASSWORD")
SQL_DRIVER      = os.getenv("SQLSERVER_DRIVER", "{ODBC Driver 18 for SQL Server}")
SQL_ENCRYPT     = os.getenv("SQLSERVER_ENCRYPT", "yes")              # yes|no
SQL_TSC         = os.getenv("SQLSERVER_TRUSTSERVERCERTIFICATE", "yes")    # yes|no
SQL_AUTOCOMMIT  = os.getenv("SQLSERVER_AUTOCOMMIT", "no")            # yes|no

ROUTE_CODE_MAXLEN = int(os.getenv("ROUTE_CODE_MAXLEN", "200"))
ROUTE_CODE_STRATEGY = os.getenv("ROUTE_CODE_STRATEGY", "truncate")  # "truncate" | "error"
TID_MAXLEN = int(os.getenv("TRANSPORTER_ID_MAXLEN", "64"))        # e.g., 64
TID_STRATEGY = os.getenv("TRANSPORTER_ID_STRATEGY", "error")      # "error" | "truncate"
DEBUG = os.getenv("DEBUG", "no").lower() == "yes"

server_part = SQL_SERVER if not SQL_PORT else f"tcp:{SQL_SERVER},{SQL_PORT}"

CONN_STR = (
    f"DRIVER={{{SQL_DRIVER}}};"
    f"SERVER={server_part};"
    f"DATABASE={SQL_DATABASE};"
    f"UID={SQL_USER};PWD={SQL_PASSWORD};"
    f"Encrypt={SQL_ENCRYPT};"
    "Authentication=SqlPassword;"
    f"TrustServerCertificate={SQL_TSC};"
)

# ----------------------------
# General helpers
# ----------------------------

NULL_LIKE = {"", " ", "null", "none", "n/a", "na", "missing", "NULL", "Missing", "N/A", "NA"}

def to_null(x):
    if pd.isna(x):
        return None
    if isinstance(x, str) and x.strip() in NULL_LIKE:
        return None
    if isinstance(x, str):
        s = x.strip()
        return s if s != "" else None
    return x

def to_int(x):
    x = to_null(x)
    if x is None: return None
    try:
        if isinstance(x, float) and math.isnan(x): return None
        return int(str(x).replace(",", "").strip())
    except Exception:
        return None

def to_decimal(x: Any, places: int | None = None) -> Optional[Decimal]:
    """Parse x into a Decimal; accept '%', commas; optionally round to `places`."""
    x = to_null(x)
    if x is None:
        return None
    try:
        s = str(x).strip().replace(",", "")
        if s.endswith("%"):
            s = s[:-1]  # keep raw percent value (e.g., '98.64' stays 98.64)
        d = Decimal(s)
        if places is not None:
            q = Decimal("1").scaleb(-places)   # e.g., places=2 -> Decimal('0.01')
            d = d.quantize(q, rounding=ROUND_HALF_UP)
        return d
    except Exception:
        return None

_NULLY = {"na", "n/a", "null", "none", "-", "—"}

def num_dec(val, places=2):
    """
    Parse numbers like '89.58', ' 89.58 ', '1,234.5', '95%', '"89.58"'.
    Returns Decimal quantized to `places`, or None if truly empty.
    """
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if s.lower() in _NULLY:
        return None
    if s.endswith("%"):
        s = s[:-1].strip()
        if s.lower() in _NULLY:
            return None
        # convert percent value to its numeric value (e.g., 95% -> 95.00). If you want 0.95, divide by 100 here.
        # s = str(Decimal(s.replace(",", "")) / Decimal(100))
    s = s.replace(",", "")
    try:
        d = Decimal(s)
    except Exception:
        # last-resort: extract first numeric token
        import re
        m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
        if not m:
            return None
        d = Decimal(m.group(0))
    q = Decimal("1." + "0"*places)
    return d.quantize(q, rounding=ROUND_HALF_UP)

def to_float_from_decimal_str(x: Any) -> Optional[float]:
    d = to_decimal(x)
    return float(d) if d is not None else None

def _to_numeric_maybe_percent(col: pd.Series) -> pd.Series:
    # Accepts strings, numbers, blanks, and percent-like values
    s = col.astype(str).str.strip()
    s = s.where(s.ne(""), None)                # empty -> None
    s = s.str.replace(",", "", regex=False)    # remove thousands sep
    s = s.str.rstrip("%")                      # drop trailing %
    out = pd.to_numeric(s, errors="coerce")    # -> float dtype with NaN
    return out

def to_date(x) -> Optional[date]:
    x = to_null(x)
    if x is None: return None
    try:
        return dtparser.parse(str(x)).date()
    except Exception:
        return None

def to_dt(x) -> Optional[datetime]:
    x = to_null(x)
    if x is None: return None
    try:
        return dtparser.parse(str(x))
    except Exception:
        return None
    
def as_opt_int(x: Any) -> Optional[int]:
    if x is None:
        return None
    # handle pandas/numpy NaN
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, np.floating) and np.isnan(x):
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None

def as_opt_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, np.floating) and np.isnan(x):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
    

_HOURS_RX = re.compile(r'(\d+)\s*hr', re.IGNORECASE)
_STATION_CODE_RX = re.compile(r'\(([A-Za-z0-9]+)\)')

def hours_from_label(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = _HOURS_RX.search(str(s))
    return int(m.group(1)) if m else None

def to_station_code(station_str: Optional[str]) -> Optional[str]:
    if not station_str:
        return None
    m = _STATION_CODE_RX.search(station_str)
    if m:
        return m.group(1).upper()
    # fallback: try token like DSW3 in text
    m2 = re.search(r'\\b(DSW\\d+)\\b', station_str, re.IGNORECASE)
    if m2:
        return m2.group(1).upper()
    # last resort: strip spaces and uppercase
    return station_str.strip().upper()[:32]

def get_iso_week_for_row_date(row_date, cursor) -> int | None:
    """
    Return {Date, WeekOfYear, WeekYear, Week_Start, Week_End} for a given date.
    Works when dbo.WeekIndex has one row per calendar day.
    """
    if isinstance(row_date, str):
        row_date = datetime.strptime(row_date, "%Y-%m-%d").date()
    if not isinstance(row_date, date):
        raise TypeError("row_date must be a datetime.date or 'YYYY-MM-DD' string")

    sql = """
        SELECT WeekOfYear
        FROM dbo.WeekIndex
        WHERE ? BETWEEN WeekStart AND WeekEnd;
    """
    row = cursor.execute(sql, row_date).fetchone()
    return int(row.WeekOfYear) if row else None

def _year_from_week_filename(fname: str) -> Optional[int]:
    """
    Extracts the year from names like:
        'DSP_Delivery_Overview_ALL_2025-W44.csv'
        'dsp_overview_snfl_dsw3_2024-w12.csv'
    Returns int year or None if pattern not found.
    """
    m = re.search(r'(\d{4})-w(\d{1,2})', fname.lower())
    if not m:
        return None
    return int(m.group(1))


# ----------------------------
# Sweeper/Rescue route helpers
# ----------------------------
def _split_pipe_list(val) -> list[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return []
    parts = str(val).split("|")
    return [p.strip() for p in parts if p.strip() != ""]

def normalize_transporter_id(x: Any) -> Optional[str]:
    """
    Normalize a transporter_id value:
      - None/blank -> None
      - strip whitespace
      - if a stray '|' remains, keep the left token
      - enforce max length (truncate or raise based on STRATEGY)
      - (optional) normalize case (commented out)
    """
    # reuse your to_null if you have it
    if x is None:
        return None
    s = str(x).strip()
    if s == "" or s.lower() in {"null", "none", "n/a", "missing"}:
        return None

    # Safety: if any leftover '|' sneaks in, keep the first token
    if "|" in s:
        s = s.split("|", 1)[0].strip()

    # Optional: normalize case
    # s = s.upper()

    if len(s) > TID_MAXLEN:
        if TID_STRATEGY.lower() == "truncate":
            if DEBUG:
                print(f"[transporter_id] truncating {len(s)} -> {TID_MAXLEN}: {s}")
            return s[:TID_MAXLEN]
        raise ValueError(
            f"transporter_id length {len(s)} exceeds max {TID_MAXLEN}: '{s}'. "
            f"Increase SQL column size or set TRANSPORTER_ID_STRATEGY=truncate."
        )
    return s

def _enforce_len_or_strategy(val: str | None, maxlen: int, strategy: str, field: str) -> str | None:
    if val is None:
        return None
    s = str(val)
    if len(s) <= maxlen:
        return s
    if strategy.lower() == "truncate":
        if DEBUG:
            print(f"[{field}] truncating {len(s)} -> {maxlen}: {s}")
        return s[:maxlen]
    raise ValueError(f"{field} length {len(s)} exceeds max {maxlen}: '{s}'")

def _expand_routes_row(row: dict) -> list[dict]:
    """
    Expand one row with '|' in transporter_id/driver_name into multiple rows.
    participant_index: 0 = primary (first), >=1 = helpers in left-to-right order.
    """
    tids  = _split_pipe_list(row.get("transporter_id"))
    names = _split_pipe_list(row.get("driver_name"))

    if not tids and not names:
        # nothing to expand: keep as single participant
        row = dict(row)
        row["participant_index"] = 0
        return [row]

    if not tids:  tids  = [None] * len(names)
    if not names: names = [None] * len(tids)

    n = max(len(tids), len(names))
    out = []
    for i in range(n):
        d = dict(row)  # base copy
        d["transporter_id"]  = normalize_transporter_id(tids[i]) if i < len(tids)  and tids[i]  is not None else None
        d["driver_name"]     = to_null(names[i])                  if i < len(names) and names[i] is not None else None
        d["participant_index"] = i
        out.append(d)
    return out

def _classify_helpers(expanded_rows: list[dict]) -> None:
    """
    Mutates rows to set 'helper_tag' to 'Rescue' or 'Sweeper' per your rules:

    - If a driver appears exactly 2 times total and an occurrence is helper (index>0),
      tag that helper occurrence as 'Rescue'.
    - If a driver never appears as primary (index==0) and appears >=1 times as helper,
      tag all their helper occurrences as 'Sweeper'.
    - Others stay untagged (None).
    """
    from collections import defaultdict
    total = defaultdict(int)
    firsts = defaultdict(int)
    nonfirsts = defaultdict(int)

    for r in expanded_rows:
        nm = (r.get("driver_name") or "").strip()
        if not nm:
            continue
        total[nm] += 1
        if r.get("participant_index", 0) == 0:
            firsts[nm] += 1
        else:
            nonfirsts[nm] += 1

    for r in expanded_rows:
        r["helper_tag"] = None
        idx = r.get("participant_index", 0)
        nm  = (r.get("driver_name") or "").strip()
        if idx == 0 or not nm:
            continue
        if total[nm] == 2 and nonfirsts[nm] == 1:
            r["helper_tag"] = "Rescue"
        elif firsts[nm] == 0 and nonfirsts[nm] >= 1:
            r["helper_tag"] = "Sweeper"

# ------------------------------
# Itinerary Station code helper
# ------------------------------
def derive_station_code(route_code: Optional[str]) -> Optional[str]:
    """
    Business rule:
      - If first two characters of the route code are 'IS' (case-insensitive) -> 'dsw2'
      - Otherwise -> 'dsw3'
      - If route_code is missing/blank -> None
    """
    if route_code is None:
        return None
    s = str(route_code).strip().upper()
    if len(s) >= 2 and s[:2] == "IS":
        return "dsw2"
    return "dsw3" 

# ----------------------------
# Filename timestamp extraction
# ----------------------------
# Supports examples like:
#   Routes_DSW3_2025-09-04_14_11 (PDT).xlsx
#   Itineraries_DSW3_2025-09-04_14_11 (PDT).xlsx
#   ...also "23-51" with hyphen, or underscore, etc.

FN_DT_REGEX = re.compile(
    r"(?P<yyyy>\d{4})[-_](?P<mm>\d{2})[-_](?P<dd>\d{2})"
    r"[_\-\s](?P<hh>\d{1,2})[:_\-](?P<mi>\d{2})(?:[:_\-](?P<ss>\d{2}))?"
    , re.IGNORECASE
)

def extract_dt_from_filename(name: str) -> Optional[datetime]:
    m = FN_DT_REGEX.search(name)
    if not m:
        return None
    yyyy = int(m.group("yyyy"))
    mm   = int(m.group("mm"))
    dd   = int(m.group("dd"))
    hh   = int(m.group("hh"))
    mi   = int(m.group("mi"))
    ss   = int(m.group("ss") or 0)
    # NOTE: filenames are labeled (PDT)/(PST) but we’ll store naive local time as-is.
    # If you want UTC, add tzinfo and convert. For now, keep it naive.
    try:
        return datetime(yyyy, mm, dd, hh, mi, ss)
    except ValueError:
        return None

# ----------------------------
# Database utilities
# ----------------------------

def get_conn():
    import pyodbc
    conn = pyodbc.connect(CONN_STR, autocommit=(SQL_AUTOCOMMIT.lower() == "yes"))
    return conn


DEBUG = os.getenv("DEBUG", "no").lower() == "yes"

def _count_qmarks(sql: str) -> int:
    # Count top-level '?' placeholders (naive but fine for VALUES (...?...) patterns)
    return sql.count("?")

def fast_insert(cursor, sql: str, rows):
    rows = list(rows)
    if not rows:
        if DEBUG: print("[fast_insert] no rows; skipping")
        return 0

    # Quick shape check: number of ? must match row length
    expected = _count_qmarks(sql)
    got = len(rows[0])
    if expected != got:
        raise ValueError(f"Placeholder/row length mismatch: SQL expects {expected} params, row has {got}")

    try:
        cursor.fast_executemany = True
        cursor.executemany(sql, rows)
        if DEBUG: print(f"[fast_insert] bulk OK: {len(rows)} rows")
        return len(rows)

    except pyodbc.Error as e:
        # Fallback to row-by-row to identify the bad one
        if DEBUG:
            print("[fast_insert] bulk insert failed; falling back to row-by-row diagnostics")
            print("ODBC error (bulk):", e)
        cursor.fast_executemany = False

        inserted = 0
        for i, r in enumerate(rows, 1):
            try:
                cursor.execute(sql, r)
                inserted += 1
            except pyodbc.Error as row_err:
                # Pretty-print the failing row and types
                typed = [(type(v).__name__, v) for v in r]
                print("\n[fast_insert] Row failure at index", i-1)
                print("SQL:", sql.strip().splitlines()[0], "...")
                print("Error:", row_err)
                print("Row (types, values):", json.dumps(typed, default=str))
                # Re-raise to stop ingestion here; comment the next line to continue past bad rows
                raise
        if DEBUG: print(f"[fast_insert] row-by-row completed: {inserted}/{len(rows)}")
        return inserted

# ----------------------------
# Per-filetype ingestion
# ----------------------------
def ingest_associates(df: pd.DataFrame, cursor):
    df = df.copy()
    _orig_cols = list(df.columns)

    # normalize headers
    df.columns = [c.strip().lower() for c in df.columns]

    # 1) Rename headers to canonical names
    ren = {
        # names
        "name and id": "full_name",          # <-- your file's column holds ONLY the name
        "full name": "full_name",
        "fullname": "full_name",
        "name": "full_name",
        "associate name": "full_name",
        "driver name": "full_name",
        "delivery associate": "full_name",
        "employee name": "full_name",
        "display name": "full_name",
        "preferred name": "full_name",

        # transporter id variants
        "transporter id": "transporter_id",
        "transporterid": "transporter_id",
        "driver id": "transporter_id",
        "driverid": "transporter_id",
        "delivery associate id": "transporter_id",
        "da id": "transporter_id",
        "employee id": "transporter_id",
        "amazon id": "transporter_id",

        # other fields you store
        "position": "position_title",
        "id expiration": "id_expiration_date",
        "id_expiration": "id_expiration_date",
        "personal phone number": "personal_phone",
        "work phone number": "work_phone",
        "status": "working_status",
    }
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})

    # Ensure required columns exist
    if "full_name" not in df.columns:
        df["full_name"] = None
    if "transporter_id" not in df.columns:
        df["transporter_id"] = None

    # Clean/trim
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip().replace({"": None, "N/A": None, "Missing": None, "NULL": None})

    # Normalize transporter_id (enforce length, strip stray pipes if any)
    df["transporter_id"] = df["transporter_id"].apply(normalize_transporter_id)

    # If first/last provided, fill missing full_name from them
    if "first name" in _orig_cols or "lastname" in _orig_cols or "last name" in _orig_cols:
        first = None
        last  = None
        for cand in ("first_name","first name","firstname","given name","f_name"):
            if cand in df.columns:
                first = df[cand].astype(str).str.strip().replace({"": None})
                break
        for cand in ("last_name","last name","lastname","surname","l_name"):
            if cand in df.columns:
                last = df[cand].astype(str).str.strip().replace({"": None})
                break
        if first is not None and last is not None:
            composed = (first.fillna("") + " " + last.fillna("")).str.strip().replace({"": None})
            df["full_name"] = df["full_name"].where(df["full_name"].notna(), composed)

    # Required fields: transporter_id (key) and full_name (NOT NULL)
    before = len(df)
    df = df[df["transporter_id"].notna()].copy()
    drop_id = before - len(df)
    before2 = len(df)
    df = df[df["full_name"].notna()].copy()
    drop_name = before2 - len(df)

    if DEBUG:
        print(f"[associates] columns (orig): {_orig_cols}")
        print(f"[associates] columns (norm): {list(df.columns)}")
        print(f"[associates] dropped missing transporter_id: {drop_id}, missing full_name: {drop_name}")
        if not df.empty:
            print("[associates] sample:", df[["transporter_id","full_name"]].head(5).to_dict(orient="records"))

    # Ensure optional columns exist so VALUES arity stays constant
    for missing in ["position_title","qualifications","id_expiration_date","personal_phone","work_phone","email","working_status"]:
        if missing not in df.columns:
            df[missing] = None

    # Build rows
    rows = []
    for _, r in df.iterrows():
        rows.append((
            r["transporter_id"],
            r.get("full_name"),
            to_null(r.get("position_title")),
            to_null(r.get("qualifications")),
            to_date(r.get("id_expiration_date")),
            to_null(r.get("personal_phone")),
            to_null(r.get("work_phone")),
            to_null(r.get("email")),
            to_null(r.get("working_status")),
        ))

    sql = """
    MERGE dsp.Associate AS tgt
    USING (VALUES (?,?,?,?,?,?,?,?,?)) AS src(
    transporter_id, full_name, position_title, qualifications,
    id_expiration_date, personal_phone, work_phone, email, working_status
    )
    ON tgt.transporter_id = src.transporter_id

    WHEN MATCHED THEN
    UPDATE SET
        full_name          = COALESCE(src.full_name,          tgt.full_name),
        position_title     = COALESCE(src.position_title,     tgt.position_title),
        qualifications     = COALESCE(src.qualifications,     tgt.qualifications),
        id_expiration_date = COALESCE(src.id_expiration_date, tgt.id_expiration_date),
        personal_phone     = COALESCE(src.personal_phone,     tgt.personal_phone),
        work_phone         = COALESCE(src.work_phone,         tgt.work_phone),
        email              = COALESCE(src.email,              tgt.email),
        working_status     = COALESCE(src.working_status,     tgt.working_status)

    WHEN NOT MATCHED BY TARGET AND src.full_name IS NOT NULL THEN
    INSERT (transporter_id, full_name, position_title, qualifications,
            id_expiration_date, personal_phone, work_phone, email, working_status)
    VALUES (src.transporter_id, src.full_name, src.position_title, src.qualifications,
            src.id_expiration_date, src.personal_phone, src.work_phone, src.email, src.working_status);
    """
    return fast_insert(cursor, sql, rows)

def ingest_routes(df: pd.DataFrame, cursor, snapshot_dt: Optional[datetime]):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "route code":"route_code",
        "dsp":"dsp_name",
        "transporter id":"transporter_id",
        "driver name":"driver_name",
        "route progress":"route_progress",
        "delivery service type":"delivery_service_type",
        "route duration":"route_duration_min",
        "all stops":"all_stops",
        "stops complete":"stops_complete",
        "not started stops":"not_started_stops"
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})

    # Build base dict rows
    base_rows: list[dict] = []
    for _, r in df.iterrows():
        base_rows.append({
            "route_code":            to_null(r.get("route_code")),
            "dsp_name":              to_null(r.get("dsp_name")),
            "transporter_id":        to_null(r.get("transporter_id")),
            "driver_name":           to_null(r.get("driver_name")),
            "route_progress":        to_null(r.get("route_progress")),
            "delivery_service_type": to_null(r.get("delivery_service_type")),
            "route_duration_min":    to_int(r.get("route_duration_min")),
            "all_stops":             to_int(r.get("all_stops")),
            "stops_complete":        to_int(r.get("stops_complete")),
            "not_started_stops":     to_int(r.get("not_started_stops")),
        })

    # Expand, classify helpers
    expanded: list[dict] = []
    for br in base_rows:
        expanded.extend(_expand_routes_row(br))
    #_classify_helpers(expanded)

    # Adjust helper rows: suffix route_code and null selected fields;
    # set delivery_service_type to Rescue/Sweeper for helpers (leave primary as-is).
    adjusted_rows: list[Tuple] = []
    helper_counters: dict[str, int] = {}

    for r in expanded:
        idx = r.get("participant_index", 0)
        base_route = r.get("route_code") or ""
        route_code_out = base_route

        # duplicate suffix for each helper in order: (1), (2), ...
        if idx > 0:
            helper_counters[base_route] = helper_counters.get(base_route, 0) + 1
            route_code_out = f"{base_route}({helper_counters[base_route]})"

        # delivery_service_type override for helpers
        dst = r.get("delivery_service_type")
        if idx > 0:
            tag = r.get("helper_tag")  # 'Rescue' | 'Sweeper' | None
            if tag:
                dst = tag  # set to Rescue/Sweeper as requested

        # null these three for helpers
        duration = None if idx > 0 else r.get("route_duration_min")
        allstops = None if idx > 0 else r.get("all_stops")
        notstarted = None if idx > 0 else r.get("not_started_stops")
        stopscomplete = None if idx > 0 else r.get("stops_complete")

        adjusted_rows.append((
            snapshot_dt or datetime.utcnow(),
            route_code_out,
            r.get("dsp_name"),
            r.get("transporter_id"),
            r.get("driver_name"),
            r.get("route_progress"),
            dst,
            duration,
            allstops,
            stopscomplete,
            notstarted,
        ))

    sql = """
        MERGE dsp.Routes AS tgt
        USING (VALUES (?,?,?,?,?,?,?,?,?,?,?)) AS src(
        snapshot_dt, route_code, dsp_name, transporter_id, driver_name,
        route_progress, delivery_service_type, route_duration_min,
        all_stops, stops_complete, not_started_stops
        )
        ON  CONVERT(varchar(33), tgt.snapshot_dt, 126) = CONVERT(varchar(33), src.snapshot_dt, 126)
        AND UPPER(LTRIM(RTRIM(tgt.route_code)))       = UPPER(LTRIM(RTRIM(src.route_code)))
        AND (
            UPPER(LTRIM(RTRIM(tgt.transporter_id))) = UPPER(LTRIM(RTRIM(src.transporter_id)))
        OR (tgt.transporter_id IS NULL AND src.transporter_id IS NULL)
            )

        WHEN MATCHED THEN UPDATE SET
        dsp_name              = src.dsp_name,
        driver_name           = src.driver_name,
        route_progress        = src.route_progress,
        delivery_service_type = src.delivery_service_type,
        route_duration_min    = src.route_duration_min,
        all_stops             = src.all_stops,
        stops_complete        = src.stops_complete,
        not_started_stops     = src.not_started_stops

        WHEN NOT MATCHED THEN
        INSERT (snapshot_dt, route_code, dsp_name, transporter_id, driver_name, route_progress,
                delivery_service_type, route_duration_min, all_stops, stops_complete, not_started_stops)
        VALUES (src.snapshot_dt, src.route_code, src.dsp_name, src.transporter_id, src.driver_name,
                src.route_progress, src.delivery_service_type, src.route_duration_min,
                src.all_stops, src.stops_complete, src.not_started_stops);
    """
    return fast_insert(cursor, sql, adjusted_rows)

def ingest_itineraries(df: pd.DataFrame, cursor, file_dt: Optional[datetime]):
    # Columns per spec; many optional. filename time -> file_datetime
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "transporter id":"transporter_id",
        "driver name":"driver_name",
        "dsp":"dsp_name",
        "da activity":"da_activity",
        "route code":"route_code",
        "progress status":"progress_status",
        "projected return to station":"projected_rts",
        "projected overtime duration (minutes)":"projected_ot_min",
        "delivery service type":"delivery_service_type",
        "cortex_vin_number":"cortex_vin_number",
        "all stops":"all_stops",
        "stops complete":"stops_complete",
        "not started stops":"not_started_stops",
        "total packages":"total_packages",
        "cortex_avg_pace_stops_per_hour":"cortex_avg_pace_sph",
        "cortex_remaining_state_of_charge":"cortex_remaining_soc",
        "app sign in:":"app_sign_in_time",
        "app sign out:":"app_sign_out_time",
        "cortex_last_stop_execution_time":"cortex_last_stop_exec_time",
        "cortex_total_break_time_used":"cortex_total_break_min",
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})

    rows = []
    for _, r in df.iterrows():
        avg_pace_sph = to_float_from_decimal_str(r.get("cortex_avg_pace_sph"))
        rem_soc      = to_float_from_decimal_str(r.get("cortex_remaining_soc"))

        # normalize transporter_id to avoid stray pipes and length issues
        tid = normalize_transporter_id(to_null(r.get("transporter_id")))

        # derive station_code for multi station dsp logic
        station_code = derive_station_code(to_null(r.get("route_code")))

        # NEW: keep the route_code exactly as it appears (no splitting)
        rc_raw = to_null(r.get("route_code"))
        rc_fixed = _enforce_len_or_strategy(
            rc_raw, ROUTE_CODE_MAXLEN, ROUTE_CODE_STRATEGY, "route_code"
        )

        rows.append((
            file_dt or datetime.utcnow(),
            tid,  # NOT NULL in table
            to_null(r.get("driver_name")),
            to_null(r.get("dsp_name")),
            to_null(r.get("da_activity")),
            rc_fixed,  # full multi-route string here
            to_null(r.get("progress_status")),
            to_null(r.get("projected_rts")),
            to_int(r.get("projected_ot_min")),
            to_null(r.get("delivery_service_type")),
            to_null(r.get("cortex_vin_number")),
            to_int(r.get("all_stops")),
            to_int(r.get("stops_complete")),
            to_int(r.get("not_started_stops")),
            to_int(r.get("total_packages")),
            avg_pace_sph,
            rem_soc,
            to_null(r.get("app_sign_in_time")),
            to_null(r.get("app_sign_out_time")),
            to_null(r.get("cortex_last_stop_exec_time")),
            to_int(r.get("cortex_total_break_min")),
            station_code,
        ))


    sql = """
        MERGE dsp.Itineraries AS tgt
        USING (VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)) AS src(
        file_datetime, transporter_id, driver_name, dsp_name, da_activity, route_code, progress_status, projected_rts,
        projected_ot_min, delivery_service_type, cortex_vin_number, all_stops, stops_complete, not_started_stops,
        total_packages, cortex_avg_pace_sph, cortex_remaining_soc, app_sign_in_time, app_sign_out_time,
        cortex_last_stop_exec_time, cortex_total_break_min, station_code
        )
        ON  tgt.file_datetime  = src.file_datetime
        AND tgt.transporter_id = src.transporter_id
        AND ((tgt.route_code = src.route_code) OR (tgt.route_code IS NULL AND src.route_code IS NULL))

        WHEN MATCHED THEN
        UPDATE SET
            driver_name              = src.driver_name,
            dsp_name                 = src.dsp_name,
            da_activity              = src.da_activity,
            progress_status          = src.progress_status,
            projected_rts            = src.projected_rts,
            projected_ot_min         = src.projected_ot_min,
            delivery_service_type    = src.delivery_service_type,
            cortex_vin_number        = src.cortex_vin_number,
            all_stops                = src.all_stops,
            stops_complete           = src.stops_complete,
            not_started_stops        = src.not_started_stops,
            total_packages           = src.total_packages,
            cortex_avg_pace_sph      = src.cortex_avg_pace_sph,
            cortex_remaining_soc     = src.cortex_remaining_soc,
            app_sign_in_time         = src.app_sign_in_time,
            app_sign_out_time        = src.app_sign_out_time,
            cortex_last_stop_exec_time = src.cortex_last_stop_exec_time,
            cortex_total_break_min   = src.cortex_total_break_min,
            station_code             = src.station_code

        WHEN NOT MATCHED THEN
        INSERT (
            file_datetime, transporter_id, driver_name, dsp_name, da_activity, route_code, progress_status, projected_rts,
            projected_ot_min, delivery_service_type, cortex_vin_number, all_stops, stops_complete, not_started_stops,
            total_packages, cortex_avg_pace_sph, cortex_remaining_soc, app_sign_in_time, app_sign_out_time,
            cortex_last_stop_exec_time, cortex_total_break_min, station_code
        )
        VALUES (
            src.file_datetime, src.transporter_id, src.driver_name, src.dsp_name, src.da_activity, src.route_code, src.progress_status, src.projected_rts,
            src.projected_ot_min, src.delivery_service_type, src.cortex_vin_number, src.all_stops, src.stops_complete, src.not_started_stops,
            src.total_packages, src.cortex_avg_pace_sph, src.cortex_remaining_soc, src.app_sign_in_time, src.app_sign_out_time,
            src.cortex_last_stop_exec_time, src.cortex_total_break_min, src.station_code
        );
    """
    return fast_insert(cursor, sql, rows)

def ingest_netradyne(df: pd.DataFrame, cursor):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "date":"event_date",
        "delivery associate":"delivery_associate",
        "transporter id":"transporter_id",
        "event id":"event_id",
        "date time (pdt/pst)":"event_datetime",
        "vin":"vin",
        "oss impact":"oss_impact",
        "metric type":"metric_type",
        "metric subtype":"metric_subtype",
        "source":"source",
        "video link":"video_link",
        "review details":"review_details"
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})
    rows = []
    for _, r in df.iterrows():
        rows.append((
            to_date(r.get("event_date")),
            to_null(r.get("delivery_associate")),
            to_null(r.get("transporter_id")),
            to_int(r.get("event_id")),
            to_dt(r.get("event_datetime")),
            to_null(r.get("vin")),
            to_null(r.get("oss_impact")),
            to_null(r.get("metric_type")),
            to_null(r.get("metric_subtype")),
            to_null(r.get("source")),
            to_null(r.get("video_link")),
            to_null(r.get("review_details")),
        ))
    sql = """
    INSERT INTO dsp.NetradyneEvents
    (event_date, delivery_associate, transporter_id, event_id, event_datetime, vin, oss_impact, metric_type, metric_subtype, source, video_link, review_details)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)

def ingest_fleet(df: pd.DataFrame, cursor):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "vin":"vin",
        "servicetype":"service_type",
        "vehiclename":"vehicle_name",
        "licenseplatenumber":"license_plate",
        "make":"make",
        "model":"model",
        "submodel":"sub_model",
        "status":"vehicle_status",
        "statuspriority":"status_priority",
        "statusreasoncode":"status_reason_code",
        "statusreasonmessage":"status_reason_msg",
        "operationalstatus":"operational_status",
        "statussearchvalue":"status_search_value",
        "subcontractorname":"subcontractor_name",
        "vehicleprovider":"vehicle_provider",
        "vehicleregistrationtype":"vehicle_reg_type",
        "year":"vehicle_year",
        "type":"vehicle_type",
        "ownershiptype":"ownership_type",
        "ownershipstartdate":"ownership_start",
        "ownershipenddate":"ownership_end",
        "pmstats":"pm_stats",
        "registrationexpirydate":"registration_expiry",
        "registeredstate":"registered_state",
        "servicetier":"service_tier",
        "stationcode":"station_code",
        "payload":"payload",
        "cubiccapacity":"cubic_capacity",
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})

    rows = []
    for _, r in df.iterrows():
        rows.append((
            to_null(r.get("vin")),
            to_null(r.get("service_type")),
            to_null(r.get("vehicle_name")),
            to_null(r.get("license_plate")),
            to_null(r.get("make")),
            to_null(r.get("model")),
            to_null(r.get("sub_model")),
            to_null(r.get("vehicle_status")),
            to_int(r.get("status_priority")),
            to_null(r.get("status_reason_code")),
            to_null(r.get("status_reason_msg")),
            to_null(r.get("operational_status")),
            to_null(r.get("status_search_value")),
            to_null(r.get("subcontractor_name")),
            to_null(r.get("vehicle_provider")),
            to_null(r.get("vehicle_reg_type")),
            to_int(r.get("vehicle_year")),
            to_null(r.get("vehicle_type")),
            to_null(r.get("ownership_type")),
            to_date(r.get("ownership_start")),
            to_date(r.get("ownership_end")),
            to_null(r.get("pm_stats")),
            to_date(r.get("registration_expiry")),
            to_null(r.get("registered_state")),
            to_null(r.get("service_tier")),
            to_null(r.get("station_code")),
            to_null(r.get("payload")),
            to_null(r.get("cubic_capacity")),
        ))

    # 28 placeholders matching the 28 tuple items above
    sql = """
    MERGE dsp.FleetVehicles AS tgt
    USING (VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?))
      AS src(
        vin, service_type, vehicle_name, license_plate, make, model, sub_model, vehicle_status, status_priority,
        status_reason_code, status_reason_msg, operational_status, status_search_value, subcontractor_name, vehicle_provider,
        vehicle_reg_type, vehicle_year, vehicle_type, ownership_type, ownership_start, ownership_end, pm_stats,
        registration_expiry, registered_state, service_tier, station_code, payload, cubic_capacity
      )
    ON tgt.vin = src.vin

    WHEN MATCHED THEN UPDATE SET
      service_type        = src.service_type,
      vehicle_name        = src.vehicle_name,
      license_plate       = src.license_plate,
      make                = src.make,
      model               = src.model,
      sub_model           = src.sub_model,
      vehicle_status      = src.vehicle_status,
      status_priority     = src.status_priority,
      status_reason_code  = src.status_reason_code,
      status_reason_msg   = src.status_reason_msg,
      operational_status  = src.operational_status,
      status_search_value = src.status_search_value,
      subcontractor_name  = src.subcontractor_name,
      vehicle_provider    = src.vehicle_provider,
      vehicle_reg_type    = src.vehicle_reg_type,
      vehicle_year        = src.vehicle_year,
      vehicle_type        = src.vehicle_type,
      ownership_type      = src.ownership_type,
      ownership_start     = src.ownership_start,
      ownership_end       = src.ownership_end,
      pm_stats            = src.pm_stats,
      registration_expiry = src.registration_expiry,
      registered_state    = src.registered_state,
      service_tier        = src.service_tier,
      station_code        = src.station_code,
      payload             = src.payload,
      cubic_capacity      = src.cubic_capacity

    WHEN NOT MATCHED THEN
      INSERT (
        vin, service_type, vehicle_name, license_plate, make, model, sub_model, vehicle_status, status_priority,
        status_reason_code, status_reason_msg, operational_status, status_search_value, subcontractor_name, vehicle_provider,
        vehicle_reg_type, vehicle_year, vehicle_type, ownership_type, ownership_start, ownership_end, pm_stats,
        registration_expiry, registered_state, service_tier, station_code, payload, cubic_capacity
      )
      VALUES (
        src.vin, src.service_type, src.vehicle_name, src.license_plate, src.make, src.model, src.sub_model, src.vehicle_status, src.status_priority,
        src.status_reason_code, src.status_reason_msg, src.operational_status, src.status_search_value, src.subcontractor_name, src.vehicle_provider,
        src.vehicle_reg_type, src.vehicle_year, src.vehicle_type, src.ownership_type, src.ownership_start, src.ownership_end, src.pm_stats,
        src.registration_expiry, src.registered_state, src.service_tier, src.station_code, src.payload, src.cubic_capacity
      );
    """
    return fast_insert(cursor, sql, rows)

def _unpivot_overview(df: pd.DataFrame, date_kind: str) -> pd.DataFrame:
    """
    For Daily/Weekly Overview:
    - Input is row-based with first column 'Metric' and columns for days or ISO weeks.
    - Output: [date_or_week, metric_name, metric_value]
    """
    df = df.copy()
    # The first column is metric name; others are dates/weeks
    df.columns = [str(c).strip() for c in df.columns]
    metric_col = df.columns[0]
    # melt
    long = df.melt(id_vars=[metric_col], var_name="k", value_name="v")
    long["metric_name"] = long[metric_col].astype(str).str.strip()
    long["v"] = _to_numeric_maybe_percent(long["v"])
    if date_kind == "date":
        long["metric_date"] = long["k"].apply(lambda s: to_date(s))
        out = long[["metric_date","metric_name","v"]].dropna(subset=["metric_date","metric_name"])
        out = out.rename(columns={"v":"metric_value"})
    else:
        # week labels like '2025-W36' or '2025-36' or 'Week 36'
        def norm_week(s: str) -> Optional[str]:
            if s is None: return None
            t = str(s).strip()
            # try to find YYYY and WW
            m = re.search(r'(\d{4}).*?(\d{2})', t)
            if m:
                return f"{m.group(1)}-W{m.group(2)}"
            return t if t else None
        long["iso_year_week"] = long["k"].apply(norm_week)
        out = long[["iso_year_week","metric_name","v"]].dropna(subset=["iso_year_week","metric_name"])
        out = out.rename(columns={"v":"metric_value"})
    return out

def ingest_daily_overview(df: pd.DataFrame, cursor):
    normalized = _unpivot_overview(df, "date")
    rows = []
    for _, r in normalized.iterrows():
        rows.append((r["metric_date"], r["metric_name"], r["metric_value"], "DailyOverview"))
    sql = """
    MERGE dsp.DailyOverview AS tgt
    USING (VALUES (?,?,?,?)) AS src(metric_date, metric_name, metric_value, source_note)
    ON tgt.metric_date = src.metric_date AND tgt.metric_name = src.metric_name
    WHEN MATCHED THEN UPDATE SET metric_value = src.metric_value, source_note = src.source_note
    WHEN NOT MATCHED THEN INSERT (metric_date, metric_name, metric_value, source_note)
         VALUES (src.metric_date, src.metric_name, src.metric_value, src.source_note);
    """
    return fast_insert(cursor, sql, rows)

def _coerce_date_series(s: pd.Series) -> pd.Series:
    # Accept strings like '2025-09-06', '09/06/2025', etc.
    # If excel serials sneak in, pandas.to_datetime handles many of them.
    # Convert to date; NaT -> NaN
    dt = pd.to_datetime(s, errors="coerce")
    out = dt.dt.date
    return out

def ingest_quality_overview_daily(df: pd.DataFrame, cursor):
    df = df.copy()
    # Normalize headers (strip spaces and lowercase)
    df.columns = [c.strip().lower() for c in df.columns]

    # Broaden the rename map to cover common variants
    rename = {
        "date": "metric_date",
        " delivery date": "metric_date",   # sometimes a leading space slips in
        "delivery date": "metric_date",
        "metric date": "metric_date",

        "delivery associate": "delivery_associate",
        "da name": "delivery_associate",
        "driver name": "delivery_associate",

        "transporter id": "transporter_id",
        "driver id": "transporter_id",
        "da id": "transporter_id",

        "packages delivered": "packages_delivered",
        "routes completed": "routes_completed",
        "dcr": "dcr_percent",
        "pod": "pod_percent",
        "dsb count": "dsb_count",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    # If we still don't have metric_date, try to find any single 'date'ish column
    if "metric_date" not in df.columns:
        for c in df.columns:
            if "date" in c:
                df = df.rename(columns={c: "metric_date"})
                break

    # Coerce metric_date safely
    if "metric_date" in df.columns:
        df["metric_date"] = _coerce_date_series(df["metric_date"])
    else:
        # No date column at all: we cannot insert; log & return 0
        if DEBUG:
            print("[quality_daily] No date-like column found. Columns:", list(df.columns))
        return 0

    # Clean/convert numeric fields
    def _to_num(colname):
        if colname in df.columns:
            s = df[colname].astype(str).str.strip().replace({"": None, "N/A": None, "Missing": None})
            # strip commas and trailing %
            s = s.str.replace(",", "", regex=False)
            if colname.endswith("_percent"):
                s = s.str.rstrip("%")
            df[colname] = pd.to_numeric(s, errors="coerce")

    for cname in (
        "packages_delivered", "routes_completed",
        "dcr_percent", "pod_percent", "dsb_count"
    ):
        _to_num(cname)

    # Drop rows with NULL metric_date (NOT NULL constraint)
    before = len(df)
    df = df[df["metric_date"].notna()].copy()
    dropped = before - len(df)
    if DEBUG and dropped:
        print(f"[quality_daily] dropped {dropped} rows missing metric_date (NOT NULL)")

    rows = []
    for _, r in df.iterrows():
        rows.append((
            r.get("metric_date"),
            to_null(r.get("delivery_associate")),
            to_null(r.get("transporter_id")),
            as_opt_int(r.get("packages_delivered")),
            as_opt_int(r.get("routes_completed")),
            as_opt_float(r.get("dcr_percent")),
            as_opt_float(r.get("pod_percent")),
            as_opt_int(r.get("dsb_count")),
        ))

    sql = """
    INSERT INTO dsp.QualityOverviewDaily
      (metric_date, delivery_associate, transporter_id,
       packages_delivered, routes_completed, dcr_percent, pod_percent, dsb_count)
    VALUES (?,?,?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)

def ingest_daily_scorecard(df: pd.DataFrame, cursor):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "week":"week_label",
        "delivery associate name":"delivery_associate_name",
        "delivery associate id":"delivery_associate_id",
        "delivered packages":"delivered_packages",
        "packages delivered not received (dnr)":"packages_dnr",
        "dsb count":"dsb_count",
        "dsb dpmo":"dsb_dpmo",
        "dispatched packages":"dispatched_packages",
        "packages returned to station (rts)":"packages_rts",
        "packages returned to station (rts) %":"packages_rts_percent",
        "return to station dpmo":"rts_dpmo"
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})
    rows = []
    for _, r in df.iterrows():
        rows.append((
            to_null(r.get("week_label")),
            to_null(r.get("delivery_associate_name")),
            to_null(r.get("delivery_associate_id")),
            to_int(r.get("delivered_packages")),
            to_int(r.get("packages_dnr")),
            to_int(r.get("dsb_count")),
            to_int(r.get("dsb_dpmo")),
            to_int(r.get("dispatched_packages")),
            to_int(r.get("packages_rts")),
            to_decimal(r.get("packages_rts_percent")),
            to_int(r.get("rts_dpmo")),
        ))
    sql = """
    INSERT INTO dsp.DailyScorecard
    (week_label, delivery_associate_name, delivery_associate_id, delivered_packages, packages_dnr, dsb_count, dsb_dpmo,
     dispatched_packages, packages_rts, packages_rts_percent, rts_dpmo)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)

def ingest_station_lvl_daily(df: pd.DataFrame, cursor):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "date":"metric_date",
        "dsp":"dsp_code",
        "dispatched packages":"dispatched_pkg",
        "delivered packages":"delivered_pkg",
        "delivered not received (dnr)":"dnr",
        "dnr dpmo":"dnr_dpmo",
        "returned to station (rts)":"rts",
        "rts %":"rts_percent",
        "rts dpmo":"rts_dpmo"
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})
    rows = []
    for _, r in df.iterrows():
        rows.append((
            to_date(r.get("metric_date")),
            to_null(r.get("dsp_code")),
            to_int(r.get("dispatched_pkg")),
            to_int(r.get("delivered_pkg")),
            to_int(r.get("dnr")),
            to_int(r.get("dnr_dpmo")),
            to_int(r.get("rts")),
            to_decimal(r.get("rts_percent")),
            to_int(r.get("rts_dpmo")),
        ))
    sql = """
    INSERT INTO dsp.StationLevelMetricsDaily
    (metric_date, dsp_code, dispatched_pkg, delivered_pkg, dnr, dnr_dpmo, rts, rts_percent, rts_dpmo)
    VALUES (?,?,?,?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)

def ingest_station_lvl_weekly(df: pd.DataFrame, cursor):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "week":"iso_year_week",
        "dsp":"dsp_code",
        "dispatched packages":"dispatched_pkg",
        "delivered packages":"delivered_pkg",
        "delivered not received (dnr)":"dnr",
        "dnr dpmo":"dnr_dpmo",
        "returned to station (rts)":"rts",
        "rts %":"rts_percent",
        "rts dpmo":"rts_dpmo"
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})
    # Normalize week format to YYYY-W## if not already
    def norm_week(w):
        if w is None: return None
        s = str(w)
        m = re.search(r'(\d{4}).*?(\d{2})', s)
        if m: return f"{m.group(1)}-W{m.group(2)}"
        return s
    df["iso_year_week"] = df["iso_year_week"].apply(norm_week)
    rows = []
    for _, r in df.iterrows():
        rows.append((
            to_null(r.get("iso_year_week")),
            to_null(r.get("dsp_code")),
            to_int(r.get("dispatched_pkg")),
            to_int(r.get("delivered_pkg")),
            to_int(r.get("dnr")),
            to_int(r.get("dnr_dpmo")),
            to_int(r.get("rts")),
            to_decimal(r.get("rts_percent")),
            to_int(r.get("rts_dpmo")),
        ))
    sql = """
    INSERT INTO dsp.StationLevelMetricsWeekly
    (iso_year_week, dsp_code, dispatched_pkg, delivered_pkg, dnr, dnr_dpmo, rts, rts_percent, rts_dpmo)
    VALUES (?,?,?,?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)

def ingest_weekly_overview(df: pd.DataFrame, cursor, file_year: Optional[int] = None):
    """
    Ingest WeeklyOverview data into dsp.WeeklyOverview.

    Assumes dsp.WeeklyOverview now has:
        [year]        int NOT NULL
        [week]        int NOT NULL
        [metric_name] nvarchar(128)
        [metric_value] decimal(18,4)
        [source_note] nvarchar(64)

    Year behavior:
        - If file_year is provided (parsed from filename), use that for ALL rows.
        - If file_year is None, we try to parse year from iso_year_week; if not
          present, we fall back to the existing table's mapping / latest year.
    """

    # 1) Unpivot -> iso_year_week, metric_name, metric_value
    normalized = _unpivot_overview(df, "week")   # produces "iso_year_week"

    # 2) Get week as an int (works for 'Week 44', '2025-W44', '2025-44', etc.)
    normalized["week"] = (
        normalized["iso_year_week"]
        .astype(str)
        .str.extract(r"(\d+)$")[0]   # grab trailing digits at the end
        .astype(int)
    )

    years: List[int] = []

    if file_year is not None:
        # 3a) Easiest case: filename name told us the year (2025 from ..._2025-W44.csv)
        years = [int(file_year)] * len(normalized)
    else:
        # 3b) Try to parse year from iso_year_week (e.g., '2025-W44')
        parsed_year = (
            normalized["iso_year_week"]
            .astype(str)
            .str.extract(r"(\d{4})")[0]
        )

        if parsed_year.notna().all():
            years = parsed_year.astype(int).tolist()
        else:
            # 3c) Fallback: infer from existing rows in the table
            cursor.execute("SELECT DISTINCT [week], [year] FROM dsp.WeeklyOverview;")
            existing = cursor.fetchall()
            week_to_year: Dict[int, int] = {}
            for row in existing:
                try:
                    w = row.week
                    y = row.year
                except AttributeError:
                    w, y = row[0], row[1]
                week_to_year[int(w)] = int(y)

            if week_to_year:
                default_year = max(week_to_year.values())
            else:
                default_year = datetime.utcnow().year

            for w in normalized["week"]:
                years.append(week_to_year.get(int(w), default_year))

    normalized["year"] = years

    # 4) Build rows for MERGE
    rows = []
    for _, r in normalized.iterrows():
        rows.append(
            (
                int(r["year"]),
                int(r["week"]),
                r["metric_name"],
                r["metric_value"],
                "WeeklyOverview",
            )
        )

    # 5) MERGE on (year, week, metric_name)
    sql = """
    MERGE dsp.WeeklyOverview AS tgt
    USING (VALUES (?,?,?,?,?)) AS src([year], [week], metric_name, metric_value, source_note)
       ON  tgt.[year]      = src.[year]
       AND tgt.[week]      = src.[week]
       AND tgt.metric_name = src.metric_name
    WHEN MATCHED THEN
        UPDATE SET
            metric_value = src.metric_value,
            source_note  = src.source_note
    WHEN NOT MATCHED THEN
        INSERT ([year], [week], metric_name, metric_value, source_note)
        VALUES (src.[year], src.[week], src.metric_name, src.metric_value, src.source_note);
    """

    return fast_insert(cursor, sql, rows)

# ----- WST bundle (ZIP with four CSVs) -----

def ingest_wst_delivered(df: pd.DataFrame, cursor):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "date":"metric_date",
        "station":"station",
        "dsp short code":"dsp_short_code",
        "package count":"package_count",
        "package details":"package_details",
        "package type":"package_type"
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})
    rows = []
    for _, r in df.iterrows():
        rows.append((
            to_date(r.get("metric_date")),
            to_null(r.get("station")),
            to_null(r.get("dsp_short_code")),
            to_int(r.get("package_count")),
            to_null(r.get("package_details")),
            to_null(r.get("package_type")),
        ))
    sql = """
    INSERT INTO dsp.WST_DeliveredPackages
    (metric_date, station, dsp_short_code, package_count, package_details, package_type)
    VALUES (?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)

def ingest_wst_service_details(df: pd.DataFrame, cursor):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "date":"row_date",
        "station":"station",
        "dsp short code":"dsp_short_code",
        "delivery associate":"delivery_associate",
        "route":"route_code",
        "service type":"service_type",
        "planned duration":"planned_duration_label",
        "log in":"login_ts",
        "log out":"logout_ts",
        "total distance planned":"total_distance_planned",
        "total distance allowance":"total_distance_allow",
        "distance unit":"distance_unit",
        "shipments delivered":"shipments_delivered",
        "shipments returned":"shipments_returned",
        "pickup packages":"pickup_packages",
        "excluded?":"excluded_flag"
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})
    rows = []
    for _, r in df.iterrows():
        rows.append((
            to_date(r.get("row_date")),
            to_null(r.get("station")),
            to_null(r.get("dsp_short_code")),
            to_null(r.get("delivery_associate")),
            to_null(r.get("route_code")),
            to_null(r.get("service_type")),
            to_null(r.get("planned_duration_label")),
            to_dt(r.get("login_ts")),
            to_dt(r.get("logout_ts")),
            to_int(r.get("total_distance_planned")),
            to_int(r.get("total_distance_allow")),
            to_null(r.get("distance_unit")),
            to_int(r.get("shipments_delivered")),
            to_int(r.get("shipments_returned")),
            to_int(r.get("pickup_packages")),
            to_null(r.get("excluded_flag")),
        ))
    sql = """
    INSERT INTO dsp.WST_ServiceDetails
    (row_date, station, dsp_short_code, delivery_associate, route_code, service_type, planned_duration_label, login_ts, logout_ts,
     total_distance_planned, total_distance_allow, distance_unit, shipments_delivered, shipments_returned, pickup_packages, excluded_flag)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)

def ingest_training_weekly_report(df: pd.DataFrame, cursor):
    """
    Ingest 'SFNL Training Weekly Report-YYYY-MM-DD.csv' into dsp.WST_TrainingWeeklyReport.

    Columns in the CSV:
        Assignment Date
        Payment Date
        Station
        DSP Short Code
        Delivery Associate
        Service Type
        Course Name
        Chapter Name
        Total Duration       (e.g. '8 hr')
        DSP Payment Eligible (e.g. 'yes'/'no')
    """
    df = df.copy()
    # normalize headers
    df.columns = [c.strip().lower() for c in df.columns]

    rename = {
        "assignment date":      "assignment_date",
        "payment date":         "payment_date",
        "station":              "station",
        "dsp short code":       "dsp_short_code",
        "delivery associate":   "delivery_associate",
        "service type":         "service_type",
        "course name":          "course_name",
        "chapter name":         "chapter_name",
        "total duration":       "total_duration_label",
        "dsp payment eligible": "dsp_payment_eligible",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    rows = []
    for _, r in df.iterrows():
        # parse dates
        assignment_date = to_date(r.get("assignment_date"))
        payment_date    = to_date(r.get("payment_date"))

        # parse hours from label like '8 hr'
        duration_label = to_null(r.get("total_duration_label"))
        total_hours    = hours_from_label(duration_label)

        rows.append((
            assignment_date,
            payment_date,
            to_null(r.get("station")),
            to_null(r.get("dsp_short_code")),
            to_null(r.get("delivery_associate")),
            to_null(r.get("service_type")),
            to_null(r.get("course_name")),
            to_null(r.get("chapter_name")),
            duration_label,
            total_hours,
            to_null(r.get("dsp_payment_eligible")),
        ))

    sql = """
    INSERT INTO dsp.WST_TrainingWeeklyReport
      (assignment_date, payment_date, station, dsp_short_code, delivery_associate,
       service_type, course_name, chapter_name, total_duration_label, total_hours,
       dsp_payment_eligible)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)


def ingest_wst_unplanned_delay(df: pd.DataFrame, cursor):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "date":"row_date",
        "station":"station",
        "dsp short code":"dsp_short_code",
        "unplanned delay":"unplanned_delay",
        "total delay in minutes":"total_delay_in_minutes",
        "impacted routes":"impacted_routes",
        "notes":"notes"
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})
    rows = []
    for _, r in df.iterrows():
        rows.append((
            to_date(r.get("row_date")),
            to_null(r.get("station")),
            to_null(r.get("dsp_short_code")),
            to_null(r.get("unplanned_delay")),
            to_int(r.get("total_delay_in_minutes")),
            to_int(r.get("impacted_routes")),
            to_null(r.get("notes")),
        ))
    sql = """
    INSERT INTO dsp.WST_UnplannedDelay
    (row_date, station, dsp_short_code, unplanned_delay, total_delay_min, impacted_routes, notes)
    VALUES (?,?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)

def ingest_wst_weekly_report(df: pd.DataFrame, cursor):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    rename = {
        "date":"row_date",
        "station":"station",
        "dsp short code":"dsp_short_code",
        "service type":"service_type",
        "planned duration":"planned_duration_label",
        "total distance planned":"total_distance_planned",
        "total distance allowance":"total_distance_allow",
        "planned distance unit":"planned_distance_unit",
        "amzl late cancel":"amzl_late_cancel",
        "dsp late cancel":"dsp_late_cancel",
        "quick coverage":"quick_coverage",
        "accepted":"accepted",
        "completed routes":"completed_routes"
    }
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})
    rows = []
    for _, r in df.iterrows():
        rows.append((
            to_date(r.get("row_date")),
            to_null(r.get("station")),
            to_null(r.get("dsp_short_code")),
            to_null(r.get("service_type")),
            to_null(r.get("planned_duration_label")),
            to_int(r.get("total_distance_planned")),
            to_int(r.get("total_distance_allow")),
            to_null(r.get("planned_distance_unit")),
            to_null(r.get("amzl_late_cancel")),
            to_null(r.get("dsp_late_cancel")),
            to_null(r.get("quick_coverage")),
            to_null(r.get("accepted")),
            to_int(r.get("completed_routes")),
        ))
    sql = """
    INSERT INTO dsp.WST_WeeklyReport
    (row_date, station, dsp_short_code, service_type, planned_duration_label, total_distance_planned, total_distance_allow,
     planned_distance_unit, amzl_late_cancel, dsp_late_cancel, quick_coverage, accepted, completed_routes)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)

def ingest_weekly_scorecard_dashboard(df: pd.DataFrame, cursor):
    "Ingest rows from the DSP Overview Dashboard CSV into dsp.WeeklyScorecard."
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]

    # Normalize and map headers
    rename = {
        # keys are lowercase versions of incoming headers
        "week": "year_week_label",
        "delivery associate": "delivery_associate_name",
        "transporter id": "transporter_id",
        "overall standing": "overall_standing",
        "overall score": "overall_score",

        "fico metric": "fico_metric",
        "fico tier": "fico_tier",
        "fico score": "fico_score",

        "speeding event rate (per trip)": "speeding_event_rate",
        "speeding event rate tier": "speeding_event_rate_tier",
        "speeding event rate score": "speeding_event_rate_score",

        "seatbelt-off rate (per trip)": "seatbelt_off_rate",
        "seatbelt-off rate tier": "seatbelt_off_rate_tier",
        "seatbelt-off rate score": "seatbelt_off_rate_score",

        "distractions rate (per trip)": "distractions_rate",
        "distractions rate tier": "distractions_rate_tier",
        "distractions rate score": "distractions_rate_score",

        "sign/ signal violations rate (per trip)": "sign_signal_viol_rate",
        "sign/ signal violations rate tier": "sign_signal_viol_rate_tier",
        "sign/ signal violations rate score": "sign_signal_viol_rate_score",

        "following distance rate (per trip)": "following_dist_rate",
        "following distance rate tier": "following_dist_rate_tier",
        "following distance rate score": "following_dist_rate_score",

        "cdf dpmo": "cdf_dpmo",
        "cdf dpmo tier": "cdf_dpmo_tier",
        "cdf dpmo score": "cdf_dpmo_score",

        "ced": "ced_metric",
        "ced tier": "ced_tier",
        "ced score": "ced_score",

        "dcr": "dcr_metric",
        "dcr tier": "dcr_tier",
        "dcr score": "dcr_score",

        "dsb": "dsb_metric",
        "dsb dpmo tier": "dsb_dpmo_tier",
        "dsb dpmo score": "dsb_dpmo_score",

        "pod": "pod_metric",
        "pod tier": "pod_tier",
        "pod score": "pod_score",

        "psb": "psb_metric",
        "psb tier": "psb_tier",
        "psb score": "psb_score",

        "packages delivered": "packages_delivered",
    }

    # Rename any columns that match exactly; also handle stray double-spaces
    df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})

    # Best-effort: if "sign/signal..." comes without a space after slash
    for k_alt in ["sign/signal violations rate (per trip)", "sign / signal violations rate (per trip)"]:
        if k_alt in df.columns:
            df = df.rename(columns={k_alt: "sign_signal_viol_rate"})
    for k_alt in ["sign/signal violations rate tier", "sign / signal violations rate tier"]:
        if k_alt in df.columns:
            df = df.rename(columns={k_alt: "sign_signal_viol_rate_tier"})
    for k_alt in ["sign/signal violations rate score", "sign / signal violations rate score"]:
        if k_alt in df.columns:
            df = df.rename(columns={k_alt: "sign_signal_viol_rate_score"})

    # Helper to coerce percent/number strings into Decimal (keeps '100.00%' as 100.00)
    def num(x):
        return to_decimal(x)

    # Build rows
    rows = []
    for _, r in df.iterrows():
        rows.append((
            to_null(r.get("year_week_label")),
            to_null(r.get("delivery_associate_name")),
            to_null(r.get("transporter_id")),

            to_null(r.get("overall_standing")),
            num_dec(r.get("overall_score"), 2),

            to_null(r.get("fico_metric")),
            to_null(r.get("fico_tier")),
            to_decimal(r.get("fico_score")),

            to_decimal(r.get("speeding_event_rate")),
            to_null(r.get("speeding_event_rate_tier")),
            to_decimal(r.get("speeding_event_rate_score")),

            to_decimal(r.get("seatbelt_off_rate")),
            to_null(r.get("seatbelt_off_rate_tier")),
            to_decimal(r.get("seatbelt_off_rate_score")),

            to_decimal(r.get("distractions_rate")),
            to_null(r.get("distractions_rate_tier")),
            to_decimal(r.get("distractions_rate_score")),

            to_decimal(r.get("sign_signal_viol_rate")),
            to_null(r.get("sign_signal_viol_rate_tier")),
            to_decimal(r.get("sign_signal_viol_rate_score")),

            to_decimal(r.get("following_dist_rate")),
            to_null(r.get("following_dist_rate_tier")),
            to_decimal(r.get("following_dist_rate_score")),

            to_decimal(r.get("cdf_dpmo")),
            to_null(r.get("cdf_dpmo_tier")),
            to_decimal(r.get("cdf_dpmo_score")),

            to_decimal(r.get("ced_metric")),
            to_null(r.get("ced_tier")),
            to_decimal(r.get("ced_score")),

            to_decimal(r.get("dcr_metric")),
            to_null(r.get("dcr_tier")),
            to_decimal(r.get("dcr_score")),

            to_decimal(r.get("dsb_metric")),
            to_null(r.get("dsb_dpmo_tier")),
            to_decimal(r.get("dsb_dpmo_score")),

            to_decimal(r.get("pod_metric")),
            to_null(r.get("pod_tier")),
            to_decimal(r.get("pod_score")),

            to_decimal(r.get("psb_metric")),
            to_null(r.get("psb_tier")),
            to_decimal(r.get("psb_score")),

            as_opt_int(r.get("packages_delivered")),
        ))

    sql = """
    INSERT INTO dsp.WeeklyScorecard (
        year_week_label,
        delivery_associate_name, transporter_id,
        overall_standing, overall_score,
        fico_metric, fico_tier, fico_score,
        speeding_event_rate, speeding_event_rate_tier, speeding_event_rate_score,
        seatbelt_off_rate, seatbelt_off_rate_tier, seatbelt_off_rate_score,
        distractions_rate, distractions_rate_tier, distractions_rate_score,
        sign_signal_viol_rate, sign_signal_viol_rate_tier, sign_signal_viol_rate_score,
        following_dist_rate, following_dist_rate_tier, following_dist_rate_score,
        cdf_dpmo, cdf_dpmo_tier, cdf_dpmo_score,
        ced_metric, ced_tier, ced_score,
        dcr_metric, dcr_tier, dcr_score,
        dsb_metric, dsb_dpmo_tier, dsb_dpmo_score,
        pod_metric, pod_tier, pod_score,
        psb_metric, psb_tier, psb_score,
        packages_delivered
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    return fast_insert(cursor, sql, rows)

import pandas as pd

def ingest_tenure_workforce_das(df: pd.DataFrame, cursor) -> int:
    """
    Ingests backlog tenure CSV files into dsp.TenureBacklogRaw.

    Expected CSV columns (case/spacing can vary):
      row id,
      dsp,
      station,
      year,
      week,
      employee id,
      transporter id,
      name,
      days since last delivered,
      delivery status,
      driver status,
      driver status reason code,
      lifetime routes,
      routes in week,
      tenure status,
      country
    """

    if df.empty:
        return 0

    # Make a copy so we don't mutate caller's DataFrame
    df = df.copy()

    # Build a mapping from normalized column name -> actual DataFrame column
    def _norm(s: str) -> str:
        return s.strip().lower()

    colmap = {_norm(c): c for c in df.columns}

    def _get(row, alias: str):
        """Get value from row using a fuzzy column name (case/space insensitive)."""
        key = _norm(alias)
        col = colmap.get(key)
        if col is None:
            return None
        return row[col]

    def _to_int(v):
        if pd.isna(v):
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    def _to_str(v):
        if pd.isna(v):
            return None
        v = str(v).strip()
        return v if v != "" else None

    rows = []
    # Use iterrows so we can use row[...] with original column names
    for _, row in df.iterrows():
        rows.append((
            _to_int(_get(row, "row id")),                      # row_id
            _to_str(_get(row, "dsp")),                         # dsp
            _to_str(_get(row, "station")),                     # station
            _to_int(_get(row, "year")),                        # year
            _to_int(_get(row, "week")),                        # week
            _to_str(_get(row, "employee id")),                 # [employee id]
            _to_str(_get(row, "transporter id")),              # [transporter id]
            _to_str(_get(row, "name")),                        # [name]
            _to_int(_get(row, "days since last delivered")),   # [days since last delivered]
            _to_str(_get(row, "delivery status")),             # [delivery status]
            _to_str(_get(row, "driver status")),               # [driver status]
            _to_str(_get(row, "driver status reason code")),   # [driver status reason code]
            _to_int(_get(row, "lifetime routes")),             # [lifetime routes]
            _to_int(_get(row, "routes in week")),              # [routes in week]
            _to_str(_get(row, "tenure status")),               # [tenure status]
            _to_str(_get(row, "country")),                     # country
        ))

    if not rows:
        return 0

    insert_sql = """
        INSERT INTO dsp.TenureBacklogRaw (
            row_id,
            dsp,
            station,
            [year],
            [week],
            [employee id],
            [transporter id],
            [name],
            [days since last delivered],
            [delivery status],
            [driver status],
            [driver status reason code],
            [lifetime routes],
            [routes in week],
            [tenure status],
            country
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    # Speed up bulk insert if using pyodbc
    try:
        cursor.fast_executemany = True
    except Exception:
        # Not critical if this fails
        pass

    cursor.executemany(insert_sql, rows)

    return len(rows)

# ----------------------------
# Dispatcher by filename
# ----------------------------
def load_frame(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    elif ext in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, dtype=str)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

def dispatch_file(path: str, cursor) -> Dict[str, Any]:
    name = os.path.basename(path)
    name_l = name.lower()

    # timestamp extraction for specific tables
    dt_from_name = extract_dt_from_filename(name)
    
    # Associates
    if name_l == "associatedata.csv" and name_l.endswith(".csv"):
        df = load_frame(path)
        n = ingest_associates(df, cursor)
        return {"table":"dsp.Associate", "rows": n}

    # Routes
    if name_l.startswith("routes_") and name_l.endswith((".xlsx",".xlsm",".xls",".csv")):
        df = load_frame(path)
        n = ingest_routes(df, cursor, snapshot_dt=dt_from_name)
        return {"table":"dsp.Routes", "rows": n, "snapshot_dt": dt_from_name}

    # Weekly Scorecard Dashboard (CSV)
    if name_l.startswith("dsp_overview_dashboard_") and name_l.endswith(".csv"):
        df = load_frame(path)
        n = ingest_weekly_scorecard_dashboard(df, cursor)
        return {"table":"dsp.WeeklyScorecard", "rows": n}

    # Itineraries
    if name_l.startswith("itineraries_") and name_l.endswith((".xlsx",".xlsm",".xls",".csv")):
        df = load_frame(path)
        n = ingest_itineraries(df, cursor, file_dt=dt_from_name)
        return {"table":"dsp.Itineraries", "rows": n, "file_datetime": dt_from_name}

    # Netradyne
    if name_l.startswith(("safety_dashboard", "safety_dashboard_sfnl", "safety_dashboard_snfl")) and name_l.endswith(".csv"):
        df = load_frame(path)
        n = ingest_netradyne(df, cursor)
        return {"table":"dsp.NetradyneEvents", "rows": n}

    # Fleet
    if name_l in ("vehiclesdata.xlsx","fleet.xlsx") or ("vehiclesdata" in name_l and name_l.endswith((".xlsx",".xlsm",".xls"))):
        df = load_frame(path)
        n = ingest_fleet(df, cursor)
        return {"table":"dsp.FleetVehicles", "rows": n}

    # Daily Overview (row-based matrix)
    if name_l.startswith("dsp_delivery_overview_all_") and "-w" not in name_l and name_l.endswith(".csv"):
        df = load_frame(path)
        n = ingest_daily_overview(df, cursor)
        return {"table":"dsp.DailyOverview", "rows": n}

    # Quality Overview (daily, per DA)
    #if name_l.startswith("quality_overview") and name_l.endswith(".csv"):
    #    df = load_frame(path)
    #    n = ingest_quality_overview_daily(df, cursor)
    #    return {"table":"dsp.QualityOverviewDaily", "rows": n}

    # Daily Scorecard
    if name_l.startswith("dsp_associates_concessions_dsw3") and name_l.endswith(".csv") and "-w" not in name_l:
        # heuristics: daily file usually lacks week suffix; weekly has 2025-W##
        df = load_frame(path)
        n = ingest_daily_scorecard(df, cursor)
        return {"table":"dsp.DailyScorecard", "rows": n}

    # Station Level Metrics Daily
    if name_l.startswith("station_level_metrics") and name_l.endswith(".csv") and "-w" not in name_l:
        df = load_frame(path)
        n = ingest_station_lvl_daily(df, cursor)
        return {"table":"dsp.StationLevelMetricsDaily", "rows": n}

    # Station Level Metrics Weekly
    if name_l.startswith("station_level_metrics") and "-w" in name_l and name_l.endswith(".csv"):
        df = load_frame(path)
        n = ingest_station_lvl_weekly(df, cursor)
        return {"table":"dsp.StationLevelMetricsWeekly", "rows": n}

    # Weekly Overview (row-based matrix with week columns)
    if name_l.startswith("dsp_delivery_overview_all_") and name_l.endswith(".csv"):
        df = load_frame(path)
        file_year = _year_from_week_filename(name)  # e.g. 2025 from '..._2025-W44.csv'
        n = ingest_weekly_overview(df, cursor, file_year=file_year)
        return {"table": "dsp.WeeklyOverview", "rows": n}
    
    if "tenure_workforce_das_report" in name_l and name_l.endswith(".csv"):
        df = load_frame(path)
        n = ingest_tenure_workforce_das(df, cursor)
        return {"table": "dsp.TenureBacklogRaw", "rows": n}
    
    # Unknown file -> warn but don’t fail
    return {"table":"(unmatched)", "rows": 0, "note": f"No handler for {name}"}

# ----------------------------
# WST ZIP processing
# ----------------------------

def process_wst_zip(zip_path: str, cursor) -> Dict[str, Any]:
    results = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for nm in zf.namelist():
            if not nm.lower().endswith(".csv"):
                continue
            with zf.open(nm) as f:
                data = f.read()
            df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
            nl = nm.lower()
            if "delivered packages report" in nl:
                n = ingest_wst_delivered(df, cursor); results.append(("dsp.WST_DeliveredPackages", n))
            elif "service details report" in nl:
                n = ingest_wst_service_details(df, cursor); results.append(("dsp.WST_ServiceDetails", n))
            elif "training weekly report" in nl:
                n = ingest_training_weekly_report(df,cursor); results.append(("dsp.WST_TrainingWeeklyReport"))
            elif "unplanned delay weekly report" in nl:
                n = ingest_wst_unplanned_delay(df, cursor); results.append(("dsp.WST_UnplannedDelay", n))
            elif "weekly report" in nl:
                n = ingest_wst_weekly_report(df, cursor); results.append(("dsp.WST_WeeklyReport", n))
            else:
                results.append(("(unmatched inside ZIP)", 0))
    return {"tables": results}

# ----------------------------
# Entrypoint
# ----------------------------

def _fetch_scalar(cur, sql: str):
    cur.execute(sql)
    row = cur.fetchone()
    return row[0] if row and len(row) > 0 else None

def ingest_path(path: str) -> List[Dict[str, Any]]:
    results = []
    with get_conn() as conn:
        cur = conn.cursor()

        # HARD ASSERT we are in the right DB (prevents 'master' surprises)
        expected_db = SQL_DATABASE
        current_db = _fetch_scalar(cur, "SELECT DB_NAME()")
        if not current_db or str(current_db).lower() != str(expected_db).lower():
            raise RuntimeError(f"[fatal] Connected to '{current_db}', expected '{expected_db}'. Check .env loading and get_conn().")

        if DEBUG:
            cur.execute("SELECT DB_NAME() dbname, OBJECT_ID('dsp.QualityOverviewDaily') qod_id")
            print("[db] sanity:", cur.fetchone())
        try:
            if os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for fname in sorted(files):
                        fpath = os.path.join(root, fname)
                        if fname.lower().endswith(".zip") and "weekly report" in fname.lower():
                            res = process_wst_zip(fpath, cur)
                            results.append({"file": fpath, **res})
                        else:
                            if fname.lower().endswith((".csv",".xlsx",".xlsm",".xls")):
                                res = dispatch_file(fpath, cur)
                                results.append({"file": fpath, **res})                          
                conn.commit()
            else:
                fname = os.path.basename(path).lower()
                if fname.endswith(".zip") and "weekly report" in fname:
                    res = process_wst_zip(path, cur)
                    results.append({"file": path, **res})
                else:
                    res = dispatch_file(path, cur)
                    results.append({"file": path, **res})  
                conn.commit()
        except Exception as e:
            conn.rollback()
            raise
    return results

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="Ingest DSP data files into SQL Server.")
    ap.add_argument("path", help="File or directory to ingest")
    args = ap.parse_args()
    out = ingest_path(args.path)
    print(json.dumps(out, default=str, indent=2))
