"""
Crunchwork RDS access.

IMPORTANT: pulse_2, purchase_orders, vendor_manager, app_services, zones and
accounts are separate DATABASES on one instance, not schemas in one database.
Postgres cannot join across them. Query each separately and join in Python -
the same pattern the trade-suggest Lambda uses.

Credentials come from the existing abcg/pulse2-rr secret. The `powerbi` user
reads all of them, so only the dbname changes per connection.

Read only. Every query is parameterised.
"""
import json
import os

import boto3
import pg8000.native

_cache = {}


def _creds():
    if "creds" not in _cache:
        sm = boto3.client("secretsmanager")
        _cache["creds"] = json.loads(
            sm.get_secret_value(SecretId=os.environ["PULSE_SECRET_ARN"])["SecretString"])
    return _cache["creds"]


def connect(database):
    c = _creds()
    return pg8000.native.Connection(
        user=c["username"],
        password=c["password"],
        host=c["host"],
        port=int(c.get("port", 5432)),
        database=database,
        timeout=30,
        ssl_context=True,
    )


def query(sql, database, **params):
    """
        query("select * from vendors where id = any(:ids)",
              database="vendor_manager", ids=[1, 2, 3])
    """
    conn = connect(database)
    try:
        rows = conn.run(sql, **params)
        cols = [c["name"] for c in conn.columns]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def describe(database, table, schema="public"):
    """Column names and types. Used by tools/discover_schema.py."""
    return query("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = :s AND table_name = :t
        ORDER BY ordinal_position
    """, database=database, s=schema, t=table)
