#!/usr/bin/env python3
# Ingest backlog itineraries Excel into dsp.Itineraries with a fixed 23:00:00 timestamp.
# Usage:
#   python ingest_backlog_itineraries.py --excel /path/to/backglog_itineraries.xlsx

import os, re, io, zipfile, math, json, argparse
from datetime import datetime, date, time, timedelta
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


# ---------- Config & connection ----------
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
SQL_ENCRYPT     = os.getenv("SQLSERVER_ENCRYPT", "yes")
SQL_TSC         = os.getenv("SQLSERVER_TRUSTSERVERCERTIFICATE", "yes")
SQL_AUTOCOMMIT  = os.getenv("SQLSERVER_AUTOCOMMIT", "no")

TID_MAXLEN = int(os.getenv("TRANSPORTER_ID_MAXLEN", "64"))
TID_STRATEGY = os.getenv("TRANSPORTER_ID_STRATEGY", "error")
ROUTE_CODE_MAXLEN = int(os.getenv("ROUTE_CODE_MAXLEN", "32"))
ROUTE_CODE_STRATEGY = os.getenv("ROUTE_CODE_STRATEGY", "truncate")
DEBUG = os.getenv("DEBUG", "no").lower() == "yes"

server_part = SQL_SERVER if not SQL_PORT else f"tcp:{SQL_SERVER},{SQL_PORT}"
CONN_STR = (
    f"DRIVER={SQL_DRIVER};"
    f"SERVER={server_part};"
    f"DATABASE={SQL_DATABASE};"
    f"UID={SQL_USER};PWD={SQL_PASSWORD};"
    f"Encrypt={SQL_ENCRYPT};"
    "Authentication=SqlPassword;"
    f"TrustServerCertificate={SQL_TSC};"
)

def get_conn():
    return pyodbc.connect(CONN_STR, autocommit=(SQL_AUTOCOMMIT.lower() == "yes"))

def parse_backlog_date_to_23(val):
    """
    Accepts values like '4/29/2025' (or Excel serials / Timestamp),
    returns a Python datetime at 23:00:00 (local/naive).
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    # robust parse (handles '4/29/2025', '2025-04-29', Excel serials, etc.)
    ts = pd.to_datetime(val, errors="coerce", origin="1899-12-30", unit="d") if isinstance(val, (int, float)) \
         else pd.to_datetime(str(val), errors="coerce")
    if pd.isna(ts):
        return None
    return datetime.combine(ts.date(), time(23, 0, 0))

# ---------- Station code helpers ----------
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

# ---------- Helpers (aligned with your main ingester) ----------
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
    x = to_null(x)
    if x is None:
        return None
    try:
        s = str(x).strip().replace(",", "")
        if s.endswith("%"):
            s = s[:-1]
        d = Decimal(s)
        if places is not None:
            q = Decimal("1").scaleb(-places)
            d = d.quantize(q, rounding=ROUND_HALF_UP)
        return d
    except Exception:
        return None

def to_float_from_decimal_str(x: Any) -> Optional[float]:
    d = to_decimal(x)
    return float(d) if d is not None else None

def to_date(x) -> Optional[date]:
    x = to_null(x)
    if x is None: return None
    try:
        return dtparser.parse(str(x)).date()
    except Exception:
        return None

def _split_pipe_list(val) -> list[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return []
    parts = str(val).replace(",", "|").split("|")  # tolerate comma- or pipe-separated
    return [p.strip() for p in parts if p.strip() != ""]

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

def normalize_transporter_id(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    if s == "" or s.lower() in {"null", "none", "n/a", "missing"}:
        return None
    # if accidental multi-value, keep first
    if "|" in s or "," in s:
        s = s.replace(",", "|").split("|", 1)[0].strip()
    if len(s) > TID_MAXLEN:
        if TID_STRATEGY.lower() == "truncate":
            if DEBUG: print(f"[transporter_id] truncating {len(s)} -> {TID_MAXLEN}: {s}")
            return s[:TID_MAXLEN]
        raise ValueError(
            f"transporter_id length {len(s)} exceeds max {TID_MAXLEN}: '{s}'. "
            f"Increase SQL column size or set TRANSPORTER_ID_STRATEGY=truncate."
        )
    return s

def fast_insert(cursor, sql: str, rows: Iterable[Tuple]):
    rows = list(rows)
    if not rows:
        if DEBUG: print("[fast_insert] no rows; skipping")
        return 0
    try:
        cursor.fast_executemany = True
        cursor.executemany(sql, rows)
        return len(rows)
    except pyodbc.Error as e:
        if DEBUG:
            print("Bulk insert failed, falling back. Error:", e)
        cursor.fast_executemany = False
        inserted = 0
        for i, r in enumerate(rows, 1):
            try:
                cursor.execute(sql, r)
                inserted += 1
            except pyodbc.Error as row_err:
                typed = [(type(v).__name__, v) for v in r]
                print("\n[fast_insert] Row failure at index", i-1)
                print("SQL head:", sql.strip().splitlines()[0], "...")
                print("Error:", row_err)
                print("Row (types, values):", json.dumps(typed, default=str))
                raise
        return inserted

# ---------- Backlog-specific normalization ----------
def normalize_backlog_headers(df: pd.DataFrame) -> pd.DataFrame:
    # Map common backlog headers to canonical ingestion names.
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    rename = {
        "day":"row_date",
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
        "app sign in":"app_sign_in_time",
        "app sign out":"app_sign_out_time",
        "cortex_last_stop_execution_time":"cortex_last_stop_exec_time",
        "cortex_total_break_time_used":"cortex_total_break_min",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    return df

def build_file_datetime_from_row_date(row_date: Optional[date]) -> datetime:
    # Fixed daily time at 23:00:00 for each row's date.
    if isinstance(row_date, str):
        try:
            row_date = dtparser.parse(row_date).date()
        except Exception:
            row_date = None
    if row_date is None:
        rd = date.today()
    else:
        rd = row_date
    return datetime.combine(rd, time(23, 0, 0))

def expand_rows_for_routes(base_row: dict) -> List[Tuple]:
    # Split 'route_code' on pipes or commas -> one itinerary row per route.
    route_list = _split_pipe_list(base_row.get("route_code"))
    if not route_list:
        route_list = [None]

    out: List[Tuple] = []
    for rc in route_list:
        rc_fixed = _enforce_len_or_strategy(rc, ROUTE_CODE_MAXLEN, ROUTE_CODE_STRATEGY, "route_code") if rc else None
        file_dt = parse_backlog_date_to_23(base_row.get("row_date"))
        out.append((
            file_dt,
            normalize_transporter_id(to_null(base_row.get("transporter_id"))),
            to_null(base_row.get("driver_name")),
            to_null(base_row.get("dsp_name")),
            to_null(base_row.get("da_activity")),
            rc_fixed,  # now one code per row, max 32 chars
            to_null(base_row.get("progress_status")),
            to_null(base_row.get("projected_rts")),
            to_int(base_row.get("projected_ot_min")),
            to_null(base_row.get("delivery_service_type")),
            to_null(base_row.get("cortex_vin_number")),
            to_int(base_row.get("all_stops")),
            to_int(base_row.get("stops_complete")),
            to_int(base_row.get("not_started_stops")),
            to_int(base_row.get("total_packages")),
            to_float_from_decimal_str(base_row.get("cortex_avg_pace_sph")),
            to_float_from_decimal_str(base_row.get("cortex_remaining_soc")),
            to_null(base_row.get("app_sign_in_time")),
            to_null(base_row.get("app_sign_out_time")),
            to_null(base_row.get("cortex_last_stop_exec_time")),
            to_int(base_row.get("cortex_total_break_min")),
            derive_station_code(to_null(base_row.get("route_code"))),
        ))

    return out

def ingest_backlog_excel(excel_path: str) -> int:
    # Read the backlog workbook (single sheet or named) and insert rows into dsp.Itineraries.
    if not os.path.exists(excel_path):
        raise FileNotFoundError(f"File not found: {excel_path}")

    # Read first sheet by default
    df = pd.read_excel(excel_path, sheet_name=0, dtype=object, engine="openpyxl")
    df = normalize_backlog_headers(df)

    # Ensure minimal columns exist
    for c in ["row_date", "transporter_id", "driver_name", "dsp_name", "route_code",
              "da_activity", "progress_status", "projected_rts", "projected_ot_min",
              "cortex_vin_number", "all_stops", "stops_complete", "not_started_stops", "total_packages"]:
        if c not in df.columns:
            df[c] = None

    # Coerce dates for row_date (for building the 23:00:00 timestamp)
    if "row_date" in df.columns:
        df["row_date"] = pd.to_datetime(df["row_date"], errors="coerce").dt.date

    # Drop rows lacking a transporter_id (table is NOT NULL + FK to Associate)
    before = len(df)
    df["transporter_id"] = df["transporter_id"].apply(normalize_transporter_id)
    df = df[df["transporter_id"].notna()].copy()
    dropped_tid = before - len(df)

    if DEBUG:
        print(f"[backlog] rows before: {before} | dropped missing transporter_id: {dropped_tid} | remaining: {len(df)}")

    # Build parameter rows
    param_rows: List[Tuple] = []
    for _, r in df.iterrows():
        base = {k: r.get(k) for k in df.columns}
        param_rows.extend(expand_rows_for_routes(base))

    if DEBUG and param_rows[:2]:
        from itertools import islice
        print("[backlog] example rows:", json.dumps(list(islice(param_rows, 0, 2)), default=str, indent=2))

    sql = """
    INSERT INTO dsp.Itineraries
    (file_datetime, transporter_id, driver_name, dsp_name, da_activity, route_code, progress_status, projected_rts,
     projected_ot_min, delivery_service_type, cortex_vin_number, all_stops, stops_complete, not_started_stops,
     total_packages, cortex_avg_pace_sph, cortex_remaining_soc, app_sign_in_time, app_sign_out_time,
     cortex_last_stop_exec_time, cortex_total_break_min,station_code)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    with get_conn() as conn:
        cur = conn.cursor()
        inserted = fast_insert(cur, sql, param_rows)
        if SQL_AUTOCOMMIT.lower() != "yes":
            conn.commit()
    return inserted

def main():
    ap = argparse.ArgumentParser(description="Ingest backlog itineraries into dsp.Itineraries with file time 23:00:00.")
    ap.add_argument("--excel", required=True, help="Path to backlog Excel file (e.g., backglog_itineraries.xlsx)")
    args = ap.parse_args()

    inserted = ingest_backlog_excel(args.excel)
    print(f"Inserted {inserted} itinerary rows.")

if __name__ == "__main__":
    main()