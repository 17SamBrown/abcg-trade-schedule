"""
Weekly dispatch, and the baseline first run.

    aws lambda invoke --function-name abcg-trade-dispatch-dev \
      --payload '{"mode":"baseline","dry_run":true,"cap":20}' out.json
"""
import os

import boto3

from common import emails, horizon, pulse, store

_ses = None


def ses():
    global _ses
    if _ses is None:
        _ses = boto3.client("ses")
    return _ses


SENDER = os.environ["SENDER_EMAIL"]
FORM_BASE = os.environ["FORM_BASE_URL"].rstrip("/")
TEST_MODE = os.environ.get("TEST_MODE") == "true"
TEST_RECIPIENT = os.environ.get("TEST_MODE_RECIPIENT")


def handler(event, context):
    event = event or {}
    try:
        return _run(event)
    except pulse.JobStatusUnavailable as e:
        # Deliberate hard stop. Sending confirmations for work that may be
        # cancelled or cash settled is worse than sending nothing at all.
        print(f"ABORTED: {e}")
        return {"aborted": True, "reason": str(e), "sent": [], "skipped": []}


def _run(event):
    only = set(event.get("vendor_ids") or [])
    dry_run = bool(event.get("dry_run"))
    mode = event.get("mode", "rolling")
    cap = int(event.get("cap") or 0) or None

    if mode == "baseline":
        # No horizon. Every open PO, because ABCG does not trust the dates it
        # holds. Jobs on an excluded status are filtered inside pulse.
        jobs = pulse.fetch_open_pos()
    else:
        jobs = pulse.fetch_scheduled_pos(
            horizon_days=int(event.get("horizon_days", horizon.DEFAULT_HORIZON_DAYS)),
            overdue_days=int(event.get("overdue_days", horizon.DEFAULT_OVERDUE_DAYS)),
        )

    # Snapshot the trade sequence per claim so the cascade runs later without
    # the API or notify functions ever reaching the database.
    claim_ids = {j["claim_id"] for j in jobs if j.get("claim_id")}
    sequences = {} if dry_run else pulse.fetch_claim_sequences(claim_ids)
    assignees = {} if dry_run else pulse.fetch_job_assignees(claim_ids)

    unassigned = 0
    for j in jobs:
        prim, cc, how = pulse.recipients_for(assignees.get(j.get("claim_id")))
        j["recipients"] = {"primary": prim, "copied": cc, "resolved": how}
        if how == "none":
            unassigned += 1
    if unassigned:
        print(f"NO ROLE ASSIGNED on {unassigned} POs - their responses will have "
              f"nobody to route to. Worth a separate look.")

    by_vendor = pulse.group_by_vendor(jobs)
    wk = store.week_of()
    sent, skipped = [], []

    for vendor_id, vendor_jobs in by_vendor.items():
        if only and vendor_id not in only:
            continue

        v = vendor_jobs[0]
        to_addr = v.get("vendor_email")
        if not to_addr:
            skipped.append({"vendor": v.get("vendor_name"),
                            "why": "no email on the vendor record",
                            "pos": len(vendor_jobs)})
            continue

        priors = {} if dry_run else store.get_prior_answers(vendor_id)

        if mode == "baseline":
            ask, carried, stats = horizon.baseline_split(vendor_jobs, priors, cap=cap)
        else:
            ask, carried = horizon.split(vendor_jobs, priors)
            stats = {"asking": len(ask), "undated": 0, "deferred": 0,
                     "carried": len(carried)}

        if not ask and not event.get("send_empty"):
            skipped.append({"vendor": v.get("vendor_name"),
                            "why": f"nothing to ask ({len(carried)} already answered)"})
            continue

        raw, token_hash = store.mint_token()
        link = f"{FORM_BASE}/?t={raw}"
        row = {"vendor": v.get("vendor_name"), "vendor_id": vendor_id,
               "ask": len(ask), "undated": stats["undated"],
               "deferred": stats["deferred"], "carried": len(carried)}

        if dry_run:
            print(f"=== DRY RUN - would send ===\nTO: {to_addr}\n"
                  f"VENDOR: {v.get('vendor_name')}\nASK: {len(ask)}  "
                  f"UNDATED: {stats['undated']}  DEFERRED: {stats['deferred']}")
            sent.append(row)
            continue

        keep = ("job_id", "claim_id", "po", "scope", "addr", "suburb", "start",
                "finish", "band", "band_label", "actions", "mode", "why",
                "pm_id", "pm_name", "pm_email", "supervisor_name",
                "supervisor_email", "rc_name", "rc_email")
        snapshot = [{k: j.get(k) for k in keep} for j in ask]
        snapshot += [{**{k: j.get(k) for k in keep}, "carried": True,
                      "prior": j.get("prior")} for j in carried]
        claim_seqs = {j["claim_id"]: sequences.get(j["claim_id"], [])
                      for j in ask if j.get("claim_id")}

        store.put_dispatch(token_hash, vendor_id, v.get("vendor_name"), wk,
                           snapshot, mode=mode,
                           allow_partial=(mode == "baseline"),
                           sequences=claim_seqs)

        subject, html = emails.trade_invite(
            {"name": v.get("vendor_name"), "contact": v.get("vendor_contact")},
            ask, link, carried=carried)
        _send(to_addr, subject, html)
        sent.append(row)

    return {"week_of": wk, "mode": mode, "sent": sent,
            "skipped": skipped, "dry_run": dry_run}


def _send(to_addr, subject, html):
    ses().send_email(
        Source=SENDER,
        Destination={"ToAddresses": [TEST_RECIPIENT if TEST_MODE else to_addr]},
        Message={
            "Subject": {"Data": (f"[TEST -> {to_addr}] " if TEST_MODE else "") + subject},
            "Body": {"Html": {"Data": html}},
        },
    )
