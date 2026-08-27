"""
On submission: split responses by whoever holds the job, email each of them,
and give them what they need to act without opening another system.

  date change  -> which downstream trades break if they accept it
  handback     -> replacement trades from the existing suggester

This module deliberately owns no HTML. Every template lives in common/emails.py.
An earlier version duplicated the label and colour maps here, which is how a new
response type reached production and crashed the digest.
"""
import json
import os

import boto3

from common import cascade, crunchwork, emails, store, suggest

_ses = None


def ses():
    global _ses
    if _ses is None:
        _ses = boto3.client("ses")
    return _ses


SENDER = os.environ["SENDER_EMAIL"]
TEST_MODE = os.environ.get("TEST_MODE") == "true"
TEST_RECIPIENT = os.environ.get("TEST_MODE_RECIPIENT")


def handler(event, context):
    return {"processed": [_process(json.loads(r["Sns"]["Message"])["token_hash"])
                          for r in event["Records"]]}


def _process(token_hash):
    item = store.table().get_item(
        Key={"pk": f"DISPATCH#{token_hash}", "sk": "META"}).get("Item")
    if not item:
        return {"token_hash": token_hash, "error": "dispatch not found"}

    vendor = item["vendor_name"]
    vendor_id = item.get("vendor_id")
    jobs = {j["job_id"]: j for j in item["jobs"]}
    sequences = item.get("sequences") or {}
    responses = item.get("responses") or {}

    impacts = _cascades(jobs, sequences, responses)
    options = _handback_options(jobs, responses, vendor)

    # Group by whoever holds the job. Two PMs means two emails; a duplicate
    # beats the right person never being told.
    buckets = {}
    for job_id, r in responses.items():
        j = jobs.get(job_id)
        if not j:
            continue
        rec = j.get("recipients") or {}
        primary = rec.get("primary") or [{"name": "Unassigned",
                                          "email": TEST_RECIPIENT}]
        copied = rec.get("copied") or []
        for p in primary:
            b = buckets.setdefault(p["email"], {
                "name": p.get("name") or "Team", "cc": set(), "rows": []})
            for c in copied:
                if c["email"] != p["email"]:
                    b["cc"].add(c["email"])
            b["rows"].append((j, r))

    sent = []
    for to_addr, b in buckets.items():
        subject, html = emails.pm_digest(b["name"], vendor, b["rows"],
                                         impacts=impacts, options=options)
        _send(to_addr, sorted(b["cc"]), subject, html)
        sent.append(to_addr)

    return {"vendor": vendor, "emails": sent,
            "cascades": len(impacts), "suggested": len(options),
            "crunchwork": _write_back(vendor, jobs, responses)}


def _cascades(jobs, sequences, responses):
    """
    Forward pass per move request, using the trade sequence snapshotted at
    token-issue time so this never touches the database.
    """
    out = {}
    for job_id, r in responses.items():
        if r.get("state") != "move":
            continue
        j = jobs.get(job_id) or {}
        acts = sequences.get(j.get("claim_id"))
        if not acts:
            continue
        try:
            out[job_id] = cascade.simulate(acts, job_id, r["new_start"])
        except Exception as e:  # noqa: BLE001
            print(f"cascade failed for {job_id} (non-fatal): {e}")
    return out


def _handback_options(jobs, responses, vendor_name):
    out = {}
    for job_id, r in responses.items():
        if r.get("state") != "handback":
            continue
        picks, ctx = suggest.suggest_for(jobs.get(job_id) or {},
                                         exclude_vendor_name=vendor_name)
        if picks:
            out[job_id] = {"picks": picks, "context": ctx}
    return out


def _send(to_addr, cc, subject, html):
    dest = {"ToAddresses": [TEST_RECIPIENT if TEST_MODE else to_addr]}
    if cc and not TEST_MODE:
        dest["CcAddresses"] = cc
    ses().send_email(
        Source=SENDER,
        Destination=dest,
        Message={
            "Subject": {"Data": (f"[TEST -> {to_addr}] " if TEST_MODE else "") + subject},
            "Body": {"Html": {"Data": html}},
        },
    )


def _write_back(vendor, jobs, responses):
    """Disabled by flag. Failures are per job and never lose the other rows."""
    written, failed = [], []
    for job_id, r in responses.items():
        j = jobs.get(job_id)
        if not j:
            continue
        try:
            written.append(crunchwork.write_back(vendor, j, r))
        except Exception as e:  # noqa: BLE001
            print(f"WRITEBACK FAILED job={job_id} po={j.get('po')}: {e}")
            failed.append({"job_id": job_id, "po": j.get("po")})
    return {"written": len(written), "failed": failed}
