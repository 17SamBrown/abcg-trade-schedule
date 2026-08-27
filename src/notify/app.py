"""
On submission: split the trade's responses by owning PM and send one email each,
CC the Repair Coordinator on that job. A PM only ever sees their own portfolio.

Move requests inside the escalation window also raise a PM task in Crunchwork.
"""
import json
import os
from datetime import date

import boto3

from common import cascade, crunchwork, store

_ses = None


def ses():
    global _ses
    if _ses is None:
        _ses = boto3.client("ses")
    return _ses

SENDER = os.environ["SENDER_EMAIL"]
TEST_MODE = os.environ.get("TEST_MODE") == "true"
TEST_RECIPIENT = os.environ.get("TEST_MODE_RECIPIENT")

LABEL = {
    "confirmed": "Dates confirmed",
    "underway":  "On site now",
    "completed": "Reported complete",
    "move":      "Date change requested",
}
COLOUR = {
    "confirmed": "#55a51c",
    "underway":  "#0078c9",
    "completed": "#00b1c1",
    "move":      "#c0392b",
}


def handler(event, context):
    results = []
    for record in event["Records"]:
        msg = json.loads(record["Sns"]["Message"])
        results.append(_process(msg["token_hash"]))
    return {"processed": results}


def _process(token_hash):
    item = store.table().get_item(
        Key={"pk": f"DISPATCH#{token_hash}", "sk": "META"}
    ).get("Item")
    if not item:
        return {"token_hash": token_hash, "error": "dispatch not found"}

    vendor = item["vendor_name"]
    jobs = {j["job_id"]: j for j in item["jobs"]}
    responses = item.get("responses") or {}

    # Group by primary recipient. A job with two Project Managers lands in two
    # buckets, so both are told. A job with nobody assigned goes to the
    # unassigned bucket rather than disappearing.
    buckets = {}
    for job_id, r in responses.items():
        j = jobs.get(job_id)
        if not j:
            continue
        rec = j.get("recipients") or {}
        primary = rec.get("primary") or []
        copied = rec.get("copied") or []

        if not primary:
            primary = [{"name": "Unassigned", "email": TEST_RECIPIENT}]

        for p in primary:
            b = buckets.setdefault(p["email"], {
                "pm_name": p.get("name") or "Team", "cc": set(), "rows": []})
            for c in copied:
                if c["email"] != p["email"]:
                    b["cc"].add(c["email"])
            b["rows"].append((j, r))

    sent = []
    for pm_email, b in buckets.items():
        if pm_email == "unassigned":
            pm_email = TEST_RECIPIENT
        _email_pm(pm_email, sorted(b["cc"]), b["pm_name"], vendor, b["rows"])
        sent.append(pm_email)

    wb = _write_back(vendor, jobs, responses)
    return {"vendor": vendor, "emails": sent, "crunchwork": wb}


def _email_pm(pm_email, cc_emails, pm_name, vendor, rows):
    moves = [r for _, r in rows if r["state"] == "move"]
    completed = [r for _, r in rows if r["state"] == "completed"]

    if moves:
        subject = f"{vendor}: {len(moves)} date change request{'s' if len(moves) != 1 else ''}"
    else:
        subject = f"{vendor}: schedule confirmed ({len(rows)} job{'s' if len(rows) != 1 else ''})"

    action_note = ""
    if moves:
        action_note = ("<p style='background:#fdf1ef;border-left:4px solid #c0392b;"
                       "padding:12px 14px;margin:0 0 18px'><b>Action needed.</b> "
                       "Nothing has moved in the scheduler. These dates change only "
                       "when you accept them.</p>")
    if completed:
        action_note += ("<p style='background:#e6f6f3;border-left:4px solid #00b1c1;"
                        "padding:12px 14px;margin:0 0 18px'>"
                        f"<b>{len(completed)} reported complete.</b> Verify against site "
                        "photos or the invoice before closing.</p>")

    body_rows = "".join(_row(j, r) for j, r in sorted(rows, key=lambda x: x[0].get("start") or ""))

    html = f"""<html><body style="font-family:Arial,sans-serif;color:#2a2723;margin:0">
<div style="max-width:640px;margin:0 auto">
  <div style="background:#00aa86;color:#fff;padding:18px 20px">
    <div style="font-size:12px;letter-spacing:.15em;text-transform:uppercase">Trade Schedule Response</div>
    <div style="font-size:21px;font-weight:bold;margin-top:5px">{vendor}</div>
  </div>
  <div style="padding:20px">
    <p>Hi {pm_name.split()[0] if pm_name else 'there'},</p>
    {action_note}
    <table style="width:100%;border-collapse:collapse;border:1px solid #e4e4e2">{body_rows}</table>
    <p style="color:#939598;font-size:13px;margin-top:20px">
      Sent automatically when {vendor} submitted their weekly confirmation.
      Only your jobs are shown.</p>
  </div>
</div></body></html>"""

    dest = {"ToAddresses": [TEST_RECIPIENT if TEST_MODE else pm_email]}
    if cc_emails and not TEST_MODE:
        dest["CcAddresses"] = cc_emails

    ses().send_email(
        Source=SENDER,
        Destination=dest,
        Message={
            "Subject": {"Data": (f"[TEST -> {pm_email}] " if TEST_MODE else "") + subject},
            "Body": {"Html": {"Data": html}},
        },
    )


def _row(j, r):
    state = r["state"]
    detail = ""
    if state == "move":
        detail = (f"<div style='margin-top:6px;font-size:14px'>"
                  f"Wants <b>{r['new_start']}</b> &middot; reason: {r['reason']}</div>")
        if r.get("note"):
            detail += (f"<div style='margin-top:4px;font-size:13px;color:#939598'>"
                       f"&ldquo;{r['note']}&rdquo;</div>")
    return f"""<tr>
  <td style="padding:12px 14px;border-bottom:1px solid #e4e4e2;border-left:4px solid {COLOUR[state]}">
    <div style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:{COLOUR[state]};font-weight:bold">
      {LABEL[state]}</div>
    <div style="font-weight:bold;margin-top:3px">{j['addr']}</div>
    <div style="font-size:13px;color:#939598">PO {j['po']} &middot; booked {j['start']} to {j['finish']}</div>
    {detail}
  </td></tr>"""


def _write_back(vendor, jobs, responses):
    """
    Every response lands in Crunchwork, because that is the system of record.
    Confirmations and site starts become activity notes, completions and date
    change requests become PM tasks. Nothing here moves a date.

    Failures are logged and swallowed per job. A GraphQL error on one job must
    not lose the other seven, and the DynamoDB record is the fallback source.
    """
    written, failed = [], []
    for job_id, r in responses.items():
        j = jobs.get(job_id)
        if not j:
            continue
        try:
            written.append(crunchwork.write_back(vendor, j, r))
        except Exception as e:                          # noqa: BLE001
            print(f"WRITEBACK FAILED job={job_id} po={j.get('po')} state={r['state']}: {e}")
            failed.append({"job_id": job_id, "po": j.get("po"), "error": str(e)})
    return {"written": len(written), "failed": failed}
