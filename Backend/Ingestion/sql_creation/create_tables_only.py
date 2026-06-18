#!/usr/bin/env python3

import argparse
import os
import sys
import pyodbc
from dotenv import load_dotenv

def get_conn(database: str):
    load_dotenv()
    server   = os.getenv("SQLSERVER_HOST")
    port     = os.getenv("SQLSERVER_PORT", "1433")
    database = os.getenv("SQLSERVER_DATABASE", "SNFL-Database")
    user     = os.getenv("SQLSERVER_USER")
    password = os.getenv("SQLSERVER_PASSWORD")
    driver   = os.getenv("SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server")
    trust    = os.getenv("SQLSERVER_TRUSTSERVERCERTIFICATE", "no")

    if not (user and password):
        raise RuntimeError("Missing SQLSERVER_USER or SQLSERVER_PASSWORD in environment (.env).")

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server},{port};"
        f"DATABASE={database};"
        f"UID={user};PWD={password};"
        f"Encrypt=yes;TrustServerCertificate={trust};"
    )
    return pyodbc.connect(conn_str, autocommit=True)

def execute_batches(cursor, sql_text: str):
    # Split on GO batch separators (case-insensitive) while preserving order
    import re
    parts = re.split(r'(?im)^\s*GO\s*$', sql_text)
    for part in parts:
        chunk = part.strip()
        if chunk:
            cursor.execute(chunk)

def main():
    parser = argparse.ArgumentParser(description="Create/verify tables only in an existing SQL Server database.")
    parser.add_argument("--db", required=True, help="Existing database name to connect to (e.g., DSP_SFNL)")
    parser.add_argument("--sql", default="create_tables.sql", help="Path to the SQL file with table DDL")
    args = parser.parse_args()

    # Read DDL from file (default: create_tables.sql in current dir)
    if not os.path.exists(args.sql):
        raise FileNotFoundError(f"Could not find SQL file: {args.sql}")

    with open(args.sql, "r", encoding="utf-8") as f:
        ddl = f.read()

    with get_conn(args.db) as conn:
        cur = conn.cursor()
        execute_batches(cur, ddl)
        print(f"[OK] Created/verified tables in database [{args.db}].")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
