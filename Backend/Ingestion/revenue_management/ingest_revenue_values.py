
#!/usr/bin/env python3
import os
import sys
import re
import argparse
from datetime import date, datetime, timedelta
import pandas as pd
import pyodbc

_STATION_CODE_RX = re.compile(r'\(([A-Za-z0-9]+)\)')

def to_station_code(station: str | None) -> str | None:
    if not station:
        return None
    m = _STATION_CODE_RX.search(station)
    if m:
        return m.group(1).upper()
    m2 = re.search(r'\b(DSW\d+)\b', station, re.IGNORECASE)
    if m2:
        return m2.group(1).upper()
    return station.strip().upper()[:32]

def iso_week_str(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"

def week_bounds_from_date(d: date):
    # ISO week: Monday is 1
    weekday = d.isoweekday()
    monday = d - timedelta(days=weekday-1)
    sunday = monday + timedelta(days=6)
    return monday, sunday

def get_conn_from_env():
    host = os.environ.get("SQLSERVER_HOST", "127.0.0.1")
    db   = os.environ.get("SQLSERVER_DB", "YourDb")
    user = os.environ.get("SQLSERVER_USER", "sa")
    pwd  = os.environ.get("SQLSERVER_PWD", "your_password")
    dsn  = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={host};DATABASE={db};UID={user};PWD={pwd};TrustServerCertificate=Yes"
    )
    return pyodbc.connect(dsn, autocommit=False)

def upsert_rate(cur, station_code: str, service_type: str, iso_year_week: str, revenue_rate: float):
    cur.execute(
        "EXEC dsp.sp_RevenueRate_Upsert @station_code=?, @service_type=?, @iso_year_week=?, @revenue_rate=?",
        (station_code, service_type, iso_year_week, revenue_rate)
    )

def normalize_service_type(s: str | None) -> str | None:
    if not s:
        return None
    s_norm = s.strip()
    # Force generics for these two
    if "package" in s_norm.lower():
        return "Package"
    if "incentive" in s_norm.lower():
        return "Incentive"
    return s_norm

def main():
    ap = argparse.ArgumentParser(description="Ingest revenuelog_values.xlsx into dsp.RevenueRate and rebuild the weekly RevenueLog.")
    ap.add_argument("excel_path", help="Path to revenuelog_values.xlsx")
    ap.add_argument("--week", help="ISO week label like 2025-W36 (default: current week)")
    ap.add_argument("--rebuild", action="store_true", help="After loading rates, rebuild RevenueLog for the week window")
    args = ap.parse_args()

    # Determine iso_year_week
    if args.week:
        iso_week = args.week
        # Compute Monday of that iso week
        y, w = map(int, iso_week.split("-W"))
        jan4 = date(y, 1, 4)
        week1_monday = jan4 - timedelta(days=jan4.isoweekday()-1)
        monday = week1_monday + timedelta(weeks=w-1)
    else:
        today = date.today()
        iso_week = iso_week_str(today)
        monday, _ = week_bounds_from_date(today)

    # Read Excel
    df = pd.read_excel(args.excel_path)
    # Expected columns: Station, Service_type, Billable hours, revenue_rate
    required = {"Station", "Service_type", "Billable hours", "revenue_rate"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Missing expected column(s): {', '.join(sorted(missing))}")

    # Clean & upsert
    rows = 0
    with get_conn_from_env() as conn:
        cur = conn.cursor()
        for _, r in df.iterrows():
            scode = to_station_code(str(r["Station"]))
            stype = normalize_service_type(str(r["Service_type"]))
            rate  = float(r["revenue_rate"]) if pd.notna(r["revenue_rate"]) else None
            if not (scode and stype and rate is not None):
                continue
            upsert_rate(cur, scode, stype, iso_week, rate)
            rows += 1

        # Optionally rebuild that week’s RevenueLog
        if args.rebuild:
            try:
                from build_revenue_log import build_revenue_log
            except ImportError:
                sys.path.append(os.path.dirname(__file__))
                from build_revenue_log import build_revenue_log

            week_monday = monday
            week_sunday = week_monday + timedelta(days=6)
            n = build_revenue_log(cur, date_from=week_monday, date_to=week_sunday)
            print(f"Rebuilt RevenueLog rows for {iso_week}: {n}")

        conn.commit()

    print(f"Upserted {rows} rate row(s) into dsp.RevenueRate for {iso_week}.")

if __name__ == "__main__":
    main()
