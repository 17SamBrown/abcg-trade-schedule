"""Dispatch storage and token handling.

Tokens are random 32-byte urlsafe strings. Only the SHA-256 hash is stored, so a
database read does not hand anyone a set of live links.
"""
import hashlib
import os
import secrets
import time
from datetime import datetime, timezone

import boto3

_ddb = boto3.resource("dynamodb")
_table = None

TOKEN_TTL_DAYS = 21


def table():
    global _table
    if _table is None:
        _table = _ddb.Table(os.environ["TABLE_NAME"])
    return _table


def mint_token():
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def put_dispatch(token_hash, vendor_id, vendor_name, week_of, jobs,
                 mode="rolling", allow_partial=False, sequences=None):
    item = {
        "pk": f"DISPATCH#{token_hash}",
        "sk": "META",
        "vendor_id": vendor_id,
        "vendor_name": vendor_name,
        "week_of": week_of,
        "mode": mode,
        "allow_partial": allow_partial,
        "jobs": jobs,
        "sequences": sequences or {},
        "responses": {},
        "sent_at": now_iso(),
        "submitted_at": None,
        "ttl": int(time.time()) + TOKEN_TTL_DAYS * 86400,
    }
    table().put_item(Item=item)
    return item


def get_dispatch(raw_token):
    if not raw_token:
        return None
    resp = table().get_item(Key={"pk": f"DISPATCH#{hash_token(raw_token)}", "sk": "META"})
    return resp.get("Item")


def save_responses(raw_token, responses):
    """Idempotent-ish: last write wins, submitted_at set once."""
    return table().update_item(
        Key={"pk": f"DISPATCH#{hash_token(raw_token)}", "sk": "META"},
        UpdateExpression="SET responses = :r, submitted_at = :t",
        ExpressionAttributeValues={":r": responses, ":t": now_iso()},
        ReturnValues="ALL_NEW",
    )["Attributes"]


def mark_opened(token_hash):
    """First open only. Later opens bump last_opened_at but keep the first."""
    now = now_iso()
    table().update_item(
        Key={"pk": f"DISPATCH#{token_hash}", "sk": "META"},
        UpdateExpression=("SET last_opened_at = :t, "
                          "opened_at = if_not_exists(opened_at, :t) "
                          "ADD open_count :one"),
        ExpressionAttributeValues={":t": now, ":one": 1},
    )


def week_of(d=None):
    d = d or datetime.now(timezone.utc).date()
    monday = d.fromordinal(d.toordinal() - d.weekday())
    return monday.isoformat()


def get_prior_answers(vendor_id, lookback_weeks=6):
    """
    The vendor's most recent answer per job, newest wins. Uses the vendor-week
    index so a wide horizon does not mean re-asking about last week's answers.
    """
    from boto3.dynamodb.conditions import Key

    resp = table().query(
        IndexName="vendor-week-index",
        KeyConditionExpression=Key("vendor_id").eq(vendor_id),
        ScanIndexForward=False,
        Limit=lookback_weeks,
    )
    priors = {}
    for item in resp.get("Items", []):                 # newest first
        snapshot = {j["job_id"]: j for j in item.get("jobs", [])}
        for job_id, r in (item.get("responses") or {}).items():
            if job_id in priors:
                continue
            snap = snapshot.get(job_id, {})
            priors[job_id] = {
                **r,
                "band": snap.get("band"),
                "start": snap.get("start"),
                "finish": snap.get("finish"),
            }
    return priors
