#!/usr/bin/env python3
import os
import sys
import pyodbc
from datetime import date, timedelta
from build_revenue_log import build_revenue_log

def get_conn():
    # Uses environment variables for secrets. Example:
    #   setx SQLSERVER_HOST "127.0.0.1"
    #   setx SQLSERVER_DB   "YourDb"
    #   setx SQLSERVER_USER "sa"
    #   setx SQLSERVER_PWD  "YourStrong!Passw0rd"
    host = os.environ.get("SQLSERVER_HOST", "127.0.0.1")
    db   = os.environ.get("SQLSERVER_DB", "YourDb")
    user = os.environ.get("SQLSERVER_USER", "sa")
    pwd  = os.environ.get("SQLSERVER_PWD", "your_password")

    dsn = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={host};DATABASE={db};UID={user};PWD={pwd};TrustServerCertificate=Yes"
    return pyodbc.connect(dsn, autocommit=False)

def main():
    # Default window: last 14 days (idempotent weekly job)
    today = date.today()
    date_from = today - timedelta(days=14)
    date_to   = today

    # Allow overriding via CLI: YYYY-MM-DD YYYY-MM-DD
    if len(sys.argv) == 3:
        date_from = date.fromisoformat(sys.argv[1])
        date_to   = date.fromisoformat(sys.argv[2])

    with get_conn() as conn:
        cur = conn.cursor()
        n = build_revenue_log(cur, date_from=date_from, date_to=date_to)
        conn.commit()
        print(f"RevenueLog built rows: {n}  (window {date_from}..{date_to})")

if __name__ == "__main__":
    main()