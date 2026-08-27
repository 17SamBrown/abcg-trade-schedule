"""
Horizon bands and answer carry-forward.

A weekly send over a 28 day window shows a trade the same job four times. Ask them
to re-answer it four times and they stop opening the email. So the window is wide
and the ask is narrow: only what is new, changed, or has gone stale.

Bands, by working proximity to the booked start:

  overdue  finish date has passed, still not reported complete
  commit   0-7 days out. This is a commitment. Hard confirm.
  plan     8-14 days out. Confirm.
  outlook  15-28 days out. Visibility only, lighter ask: looks right, or flag it.

An answer given in the outlook band does not survive into the commit band. Someone
saying "yes, four weeks out" is not the same person saying "yes, Monday", and the
whole point of the near band is that you can rely on it.
"""
from datetime import date

BANDS = [
    ("overdue", None, None),
    ("commit", 0, 7),
    ("plan", 8, 14),
    ("outlook", 15, 28),
]

BAND_LABEL = {
    "overdue": "Past its finish date",
    "commit": "This week",
    "plan": "Next week",
    "outlook": "Coming up",
}

# outlook jobs get a lighter set of options
BAND_ACTIONS = {
    "overdue": ["confirmed", "underway", "completed", "move"],
    "commit": ["confirmed", "underway", "completed", "move"],
    "plan": ["confirmed", "completed", "move"],
    "outlook": ["looks_right", "move"],
}

DEFAULT_HORIZON_DAYS = 28
DEFAULT_OVERDUE_DAYS = 21


def _d(v):
    return v if isinstance(v, date) else date.fromisoformat(v)


def band_for(job, today=None):
    today = today or date.today()
    start, finish = _d(job["start"]), _d(job["finish"])
    if finish < today:
        return "overdue"
    days = (start - today).days
    if days <= 7:
        return "commit"
    if days <= 14:
        return "plan"
    return "outlook"


def needs_answer(job, prior, today=None):
    """
    prior: the vendor's most recent answer for this job, or None.
           {state, at, band, start, finish, reason?, new_start?}

    Returns (bool, why). The why is what the form shows as the reason it is asking.
    """
    today = today or date.today()
    band = band_for(job, today)

    if not prior:
        return True, "new"

    # the schedule moved under them, so the old answer is about different dates
    if prior.get("start") != job["start"] or prior.get("finish") != job["finish"]:
        return True, "dates changed"

    state = prior.get("state")

    # a pending move request is with the PM, not the trade
    if state == "move":
        return False, "waiting on ABCG"

    # they said done, do not keep asking
    if state == "completed":
        return False, "reported complete"

    # said on site, but the window has since closed
    if state == "underway" and band == "overdue":
        return True, "was underway, finish date now passed"

    # a soft outlook answer does not carry into the near bands
    if prior.get("band") == "outlook" and band in ("commit", "plan"):
        return True, "getting close, confirm it"

    if state == "looks_right" and band in ("commit", "plan"):
        return True, "getting close, confirm it"

    return False, f"confirmed {prior.get('at', '')[:10]}"


def split(jobs, priors, today=None):
    """
    Returns (ask, carried). `ask` is what the form demands an answer on,
    ordered by urgency. `carried` is shown collapsed, changeable but not required.
    """
    today = today or date.today()
    ask, carried = [], []
    order = {"overdue": 0, "commit": 1, "plan": 2, "outlook": 3}

    for j in jobs:
        band = band_for(j, today)
        prior = priors.get(j["job_id"])
        want, why = needs_answer(j, prior, today)
        entry = dict(j)
        entry.update({
            "band": band,
            "band_label": BAND_LABEL[band],
            "actions": BAND_ACTIONS[band],
            "why": why,
        })
        if want:
            ask.append(entry)
        else:
            entry["prior"] = prior
            carried.append(entry)

    ask.sort(key=lambda x: (order[x["band"]], x["start"]))
    carried.sort(key=lambda x: x["start"])
    return ask, carried


# --------------------------------------------------------------------------
# Baseline mode - the first run
# --------------------------------------------------------------------------
#
# The first send has no horizon. Every open PO goes to the trade, because ABCG
# does not trust any of the dates it holds. Two questions get asked, not one:
#
#   has dates    -> confirm or move, as normal
#   has no dates -> propose a start and finish. This is the only mode where a
#                   trade supplies dates from nothing.
#
# Baseline also allows partial submission. A trade with forty POs will not do
# them in one sitting, and forcing all-or-nothing loses the whole submission.

def baseline_split(jobs, priors=None, cap=None, today=None):
    """
    Returns (ask, carried, stats). `ask` covers every open PO with no date filter.
    `cap` limits the first send per vendor, oldest first, so a trade with a very
    long list is not handed all of it at once.
    """
    today = today or date.today()
    priors = priors or {}
    ask, carried = [], []

    for j in jobs:
        has_dates = bool(j.get("start")) and bool(j.get("finish"))
        prior = priors.get(j["job_id"])

        if prior and prior.get("state") in ("completed",):
            carried.append({**j, "prior": prior, "why": "reported complete"})
            continue

        if has_dates:
            band = band_for(j, today)
            entry = {**j, "band": band, "band_label": BAND_LABEL[band],
                     "actions": ["confirmed", "underway", "completed", "move"],
                     "mode": "confirm", "why": "baseline"}
        else:
            entry = {**j, "band": "undated", "band_label": "No dates set",
                     "actions": ["propose", "completed"],
                     "mode": "propose", "why": "no dates on file"}
        ask.append(entry)

    # undated first, then oldest raised, so the cap keeps the most useful ones
    ask.sort(key=lambda x: (x["mode"] != "propose", x.get("start") or "0000",
                            x.get("raised") or "0000"))

    deferred = []
    if cap and len(ask) > cap:
        deferred = ask[cap:]
        ask = ask[:cap]

    stats = {
        "total_open": len(jobs),
        "asking": len(ask),
        "undated": sum(1 for j in ask if j["mode"] == "propose"),
        "deferred": len(deferred),
        "carried": len(carried),
    }
    return ask, carried, stats
