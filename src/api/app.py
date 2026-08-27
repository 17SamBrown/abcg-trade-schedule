"""Trade-facing API. Token in the query string is the only credential."""
import json
import os
from datetime import date

import boto3

from common import store

_sns = None


def sns():
    global _sns
    if _sns is None:
        _sns = boto3.client("sns")
    return _sns

VALID_STATES = {"confirmed", "underway", "completed", "move",
                "looks_right", "propose", "handback"}
VALID_REASONS = {"Weather", "Capacity", "Customer delay", "Material delay",
                 "Access denied", "Waiting on ABCG"}

# Why a trade cannot do the job at all. Deliberately separate from delay
# reasons: a delay moves a date, a handback means nobody is allocated.
VALID_HANDBACK_REASONS = {
    "No capacity at all",
    "Outside our area",
    "Outside our scope or licence",
    "Rate or scope not viable",
    "Compliance or insurance lapsed",
    "Issue with the customer",
    "Other",
}


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    token = (event.get("queryStringParameters") or {}).get("t")

    dispatch = store.get_dispatch(token)
    if not dispatch:
        return _json(404, {"error": "This link is not valid or has expired. "
                                    "Call your project manager and we will send a new one."})

    if method == "GET":
        return _get(dispatch)
    if method == "POST":
        return _post(event, token, dispatch)
    return _json(405, {"error": "Method not allowed"})


def _get(dispatch):
    # Record that the form was actually opened. Distinguishes a trade who never
    # saw the email from one who opened it and walked away - same silence, very
    # different follow-up call.
    try:
        store.mark_opened(dispatch["pk"].split("#", 1)[1])
    except Exception as e:  # noqa: BLE001
        print(f"open tracking failed (non-fatal): {e}")

    return _json(200, {
        "vendor_name": dispatch["vendor_name"],
        "week_of": dispatch["week_of"],
        "submitted_at": dispatch.get("submitted_at"),
        "today": date.today().isoformat(),
        "reasons": sorted(VALID_REASONS),
        # never expose PM contact details to the trade
        "jobs": [
            {k: j.get(k) for k in ("job_id", "po", "scope", "addr", "suburb", "start", "finish")}
            for j in dispatch["jobs"]
        ],
    })


def _post(event, token, dispatch):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _json(400, {"error": "We could not read that. Try again."})

    incoming = body.get("responses") or {}
    known = {j["job_id"] for j in dispatch["jobs"]}
    clean = {}

    for job_id, r in incoming.items():
        if job_id not in known:
            continue
        state = r.get("state")
        if state not in VALID_STATES:
            return _json(400, {"error": f"Unknown response for job {job_id}."})
        entry = {"state": state, "at": store.now_iso()}
        if state == "handback":
            reason = r.get("reason")
            if reason not in VALID_HANDBACK_REASONS:
                return _json(400, {"error": "Please tell us why you cannot do this job."})
            entry.update({"reason": reason, "note": (r.get("note") or "")[:500]})

        if state == "propose":
            ps, pf = r.get("proposed_start"), r.get("proposed_finish")
            if not ps or not pf:
                return _json(400, {"error": "We need a start and a finish date for that job."})
            try:
                if date.fromisoformat(pf) < date.fromisoformat(ps):
                    return _json(400, {"error": "The finish date cannot be before the start."})
                if date.fromisoformat(ps) < date.today():
                    return _json(400, {"error": "The start date cannot be in the past."})
            except ValueError:
                return _json(400, {"error": "That date did not look right."})
            entry.update({"proposed_start": ps, "proposed_finish": pf,
                          "note": (r.get("note") or "")[:500]})

        if state == "move":
            reason, new_start = r.get("reason"), r.get("new_start")
            if reason not in VALID_REASONS or not new_start:
                return _json(400, {"error": "A date change needs a reason and a new start date."})
            try:
                if date.fromisoformat(new_start) < date.today():
                    return _json(400, {"error": "The new start date cannot be in the past."})
            except ValueError:
                return _json(400, {"error": "That date did not look right."})
            entry.update({"reason": reason, "new_start": new_start,
                          "note": (r.get("note") or "")[:500]})
        clean[job_id] = entry

    missing = known - set(clean)
    if missing and not dispatch.get("allow_partial"):
        return _json(400, {"error": f"{len(missing)} job(s) still need an answer."})

    updated = store.save_responses(token, clean)

    sns().publish(
        TopicArn=os.environ["NOTIFY_TOPIC_ARN"],
        Subject=f"Trade response: {dispatch['vendor_name']}"[:99],
        Message=json.dumps({"token_hash": updated["pk"].split("#", 1)[1],
                            "vendor_name": dispatch["vendor_name"]}),
    )
    return _json(200, {"ok": True, "count": len(clean), "remaining": len(missing)})


def _json(code, body):
    return {
        "statusCode": code,
        "headers": {"content-type": "application/json",
                    "cache-control": "no-store"},
        "body": json.dumps(body, default=str),
    }
