#!/usr/bin/env python3
import argparse
import os
import sys
import re
import pyodbc
from typing import Optional
from dotenv import load_dotenv

# pyodbc constant for access token (SQL_COPT_SS_ACCESS_TOKEN)
SQL_COPT_SS_ACCESS_TOKEN = 1256

AZURE_SQL_SCOPE = "https://database.windows.net/.default"

def _bool_env(name: str, default: bool=False) -> str:
    val = os.getenv(name)
    if val is None:
        return "yes" if default else "no"
    return "yes" if str(val).strip().lower() in {"1","true","yes","y"} else "no"

def base_conn_parts(server: str, port: str, database: str, driver: str) -> list[str]:
    # IMPORTANT: DRIVER must be exactly one pair of braces: DRIVER={ODBC Driver 18 for SQL Server}
    return [
        f"DRIVER={{{driver}}}",
        f"SERVER=tcp:{server},{port}",
        f"DATABASE={database}",
        "Encrypt=yes",
        f"TrustServerCertificate={_bool_env('SQLSERVER_TRUSTSERVERCERTIFICATE', )}",
        f"Connection Timeout={os.getenv('SQLSERVER_TIMEOUT','30')}",
    ]

def build_conn_str_sql_auth(server: str, port: str, database: str, driver: str, user: str, password: str) -> str:
    parts = base_conn_parts(server, port, database, driver)
    parts.append("Authentication=SqlPassword")
    parts.append(f"UID={user}")
    parts.append(f"PWD={password}")
    return ";".join(parts) + ";"

def build_conn_str_azure_ad(server: str, port: str, database: str, driver: str, mode: str, user: Optional[str], password: Optional[str]) -> str:
    parts = base_conn_parts(server, port, database, driver)
    parts.append(f"Authentication={mode}")
    if user:
        parts.append(f"UID={user}")
    if password:
        parts.append(f"PWD={password}")
    return ";".join(parts) + ";"

def get_access_token() -> bytes:
    """Acquire an AAD access token for Azure SQL using azure-identity.
    On local dev, prefer Azure CLI -> Default (no MSI) -> MSI.
    """
    try:
        from azure.identity import (
            ManagedIdentityCredential,
            AzureCliCredential,
            DefaultAzureCredential,
            ClientSecretCredential,
        )
    except Exception as e:
        raise RuntimeError("azure-identity is required for AccessToken mode. pip install azure-identity") from e

    tenant = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")

    # Prefer explicit SP if provided
    if tenant and client_id and client_secret:
        cred = ClientSecretCredential(tenant_id=tenant, client_id=client_id, client_secret=client_secret)
        token = cred.get_token(AZURE_SQL_SCOPE).token
        return token.encode("utf-16-le")

    # Local dev preference: CLI first, then Default (exclude MSI), then MSI last
    try_order = []
    try_order.append(("AzureCliCredential", AzureCliCredential()))
    try_order.append(("DefaultAzureCredential(exclude_msi)", DefaultAzureCredential(exclude_managed_identity_credential=True)))
    try_order.append(("ManagedIdentityCredential", ManagedIdentityCredential()))

    for name, cand in try_order:
        try:
            _ = cand.get_token(AZURE_SQL_SCOPE)
            token = cand.get_token(AZURE_SQL_SCOPE).token
            return token.encode("utf-16-le")
        except Exception:
            continue

    raise RuntimeError("Failed to acquire an access token. Run 'az login' for CLI auth, or configure AZURE_* env vars, or run in Azure with a Managed Identity.")

def connect_with_access_token(conn_str_no_auth: str):
    token_bytes = get_access_token()
    if "Authentication=ActiveDirectoryAccessToken" not in conn_str_no_auth:
        if conn_str_no_auth.endswith(";"):
            conn_str = conn_str_no_auth + "Authentication=ActiveDirectoryAccessToken;"
        else:
            conn_str = conn_str_no_auth + ";Authentication=ActiveDirectoryAccessToken;"
    else:
        conn_str = conn_str_no_auth
    return pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_bytes})

def execute_batches(cursor, sql_text: str):
    parts = re.split(r'(?im)^\s*GO\s*$', sql_text)
    for part in parts:
        chunk = part.strip()
        if chunk:
            cursor.execute(chunk)

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Create/verify tables only in an existing Azure SQL database (with Microsoft Entra ID support).")
    parser.add_argument("--db", required=True, help="Existing database name to connect to (e.g., DSP_SFNL)")
    parser.add_argument("--sql", default="create_tables.sql", help="Path to the SQL file with table DDL")
    args = parser.parse_args()

    if not os.path.exists(args.sql):
        raise FileNotFoundError(f"Could not find SQL file: {args.sql}")

    with open(args.sql, "r", encoding="utf-8") as f:
        ddl = f.read()

    driver   = os.getenv("SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server")
    server   = os.getenv("SQLSERVER_HOST")  # e.g., myserver.database.windows.net
    port     = os.getenv("SQLSERVER_PORT", "1433")
    database = args.db or os.getenv("SQLSERVER_DATABASE")

    if not server:
        raise RuntimeError("Missing SQLSERVER_HOST in environment (.env). Example: myserver.database.windows.net")
    if not database:
        raise RuntimeError("Database name was not provided via --db or SQLSERVER_DATABASE.")

    auth_mode = os.getenv("SQLSERVER_AUTHENTICATION", "").strip()
    user      = os.getenv("SQLSERVER_USER")
    password  = os.getenv("SQLSERVER_PASSWORD")

    try:
        if auth_mode:
            if auth_mode == "ActiveDirectoryAccessToken":
                base = ";".join(base_conn_parts(server, port, database, driver)) + ";"
                with connect_with_access_token(base) as conn:
                    conn.autocommit = True
                    cur = conn.cursor()
                    execute_batches(cur, ddl)
            elif auth_mode in {
                "ActiveDirectoryInteractive",
                "ActiveDirectoryPassword",
                "ActiveDirectoryIntegrated",
                "ActiveDirectoryMsi",
                "ActiveDirectoryServicePrincipal",
            }:
                conn_str = build_conn_str_azure_ad(server, port, database, driver, auth_mode, user, password)
                with pyodbc.connect(conn_str, autocommit=True) as conn:
                    cur = conn.cursor()
                    execute_batches(cur, ddl)
            else:
                raise RuntimeError(f"Unsupported SQLSERVER_AUTHENTICATION value: {auth_mode}")
        else:
            if not (user and password):
                raise RuntimeError("For SQL auth, set SQLSERVER_USER and SQLSERVER_PASSWORD or specify SQLSERVER_AUTHENTICATION for Entra.")
            conn_str = build_conn_str_sql_auth(server, port, database, driver, user, password)
            with pyodbc.connect(conn_str, autocommit=True) as conn:
                cur = conn.cursor()
                execute_batches(cur, ddl)

        print(f"[OK] Created/verified tables in database [{database}] on server [{server}].")

    except pyodbc.Error as e:
        print("ODBC Error:", e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
