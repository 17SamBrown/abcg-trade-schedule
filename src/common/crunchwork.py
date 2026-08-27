"""
Crunchwork write-back.

ONE RULE: this module never changes a scheduled date, a PO date, or a job status.
It writes tasks and activity notes. A human inside Crunchwork makes the change,
which is what keeps a reason code and an actor on every date movement when an
insurer audits the claim lifecycle.

Policy per trade response:

  confirmed  -> activity note on the job. No task. Silence is the point.
  underway   -> activity note. Feeds site attendance without touching status.
  completed  -> task on PM: verify against photos or invoice, then close.
  move       -> task on PM carrying the proposed date in a typed field, so the
                PM's "Accept proposed date" action is what actually moves it.

The Accept action is a Crunchwork workflow, not something in this repo. That is
deliberate. Build it once in the composer engine and every date change in the
business flows through the same audited path.
"""
import os

from common import pulse

TEST_MODE = os.environ.get("TEST_MODE") == "true"

# Master switch. Off until the Accept proposed date workflow exists in Crunchwork.
# With this off, nothing in this repo writes to pulse_2 at all.
WRITE_BACK_ENABLED = os.environ.get("WRITE_BACK_ENABLED") == "true"

# --- custom fields the task carries so the Crunchwork workflow can act on it ---
# TODO confirm these field keys exist on your task type before go-live
FIELD_PROPOSED_START = "trade_proposed_start"
FIELD_DELAY_REASON = "trade_delay_reason"
FIELD_SOURCE = "trade_response_source"


def write_back(vendor_name, job, response):
    """Returns a dict describing what was written, for the run log."""
    if not WRITE_BACK_ENABLED:
        return {"job_id": job["job_id"], "action": "skipped",
                "why": "WRITE_BACK_ENABLED is false"}
    state = response["state"]
    handler = {
        "confirmed": _confirmed,
        "underway": _underway,
        "completed": _completed,
        "move": _move,
    }[state]
    return handler(vendor_name, job, response)


def _confirmed(vendor_name, job, response):
    _note(job, f"{vendor_name} confirmed the booked dates "
               f"({job['start']} to {job['finish']}) via the weekly trade form.")
    return {"job_id": job["job_id"], "action": "note"}


def _underway(vendor_name, job, response):
    _note(job, f"{vendor_name} reported this job is underway on site. "
               f"Booked {job['start']} to {job['finish']}. Status unchanged.")
    return {"job_id": job["job_id"], "action": "note"}


def _completed(vendor_name, job, response):
    _task(
        job,
        title=f"Verify completion claimed by {vendor_name}",
        body=(
            f"{vendor_name} reported PO {job['po']} at {job['addr']} as complete "
            f"via the weekly trade form.\n\n"
            f"Booked window was {job['start']} to {job['finish']}.\n\n"
            f"This is the trade's claim, not a verified completion. Check site photos "
            f"or the invoice before changing job status."
        ),
        fields={FIELD_SOURCE: "weekly_trade_form"},
    )
    return {"job_id": job["job_id"], "action": "task", "type": "verify_completion"}


def _move(vendor_name, job, response):
    _task(
        job,
        title=f"Date change requested by {vendor_name} - {response['reason']}",
        body=(
            f"{vendor_name} has asked to move PO {job['po']} at {job['addr']}.\n\n"
            f"Currently booked: {job['start']} to {job['finish']}\n"
            f"Proposed start:   {response['new_start']}\n"
            f"Reason:           {response['reason']}\n"
            f"Trade note:       {response.get('note') or 'none'}\n\n"
            f"Nothing has moved. Use Accept proposed date to update the schedule, or "
            f"reject and call the trade. Either way the reason code stays on the job."
        ),
        fields={
            FIELD_PROPOSED_START: response["new_start"],
            FIELD_DELAY_REASON: response["reason"],
            FIELD_SOURCE: "weekly_trade_form",
        },
    )
    return {"job_id": job["job_id"], "action": "task", "type": "date_change_request"}


# --- thin wrappers so there is exactly one place each mutation is called ---

def _task(job, title, body, fields=None):
    if TEST_MODE:
        print(f"[TEST MODE] task on {job['job_id']} ({job['po']}): {title} | {fields}")
        return None
    return pulse.create_pm_task(
        job_id=job["job_id"],
        title=title,
        body=body,
        assignee_id=job.get("pm_id"),
        fields=fields or {},
    )


def _note(job, text):
    if TEST_MODE:
        print(f"[TEST MODE] note on {job['job_id']} ({job['po']}): {text}")
        return None
    return pulse.create_activity_note(job_id=job["job_id"], text=text)
