"""
Replacement trades for a handed-back job.

Calls the existing abcg-trade-suggest Lambda, which reads the nightly scorecard
from abcg-vendor-scorer and joins live purchase order context. DNU zone
blocking, compliance and suppression are handled inside that Lambda, so none of
it is repeated here.

CONTRACT. The suggester returns TWO DIFFERENT SHAPES depending on the request,
both confirmed by invoking it.

  Without `trade` - a per-trade breakdown of the job's DRAFT purchase orders:

    {"job_reference":..., "breakdown": [
        {"trade":..., "zone":..., "vendors_in_zone": N,
         "best_value": {...} | null,      <- single objects
         "fastest": {...} | null,
         "worth_trying": {...} | null}]}

  With `trade` - the full three ranked lists for that trade:

    {"trade":..., "zone":..., "vendors_in_trade": N, "vendors_in_zone": N,
     "trade_median_days": N, "capacity_threshold_30d": N,
     "best_value_with_capacity": [ {...}, ... ],   <- lists
     "fastest_with_capacity":    [ {...}, ... ],
     "worth_trying":             [ {...}, ... ]}

Vendor objects carry vendor, phone, email, open_jobs_30d, pos_this_trade_12m,
cost_vs_peers, resend_pct, reconciles_at, history.

ONE LIMITATION WORTH KNOWING: the suggester is built around DRAFT purchase
orders, since its normal job is allocating work that has not gone out yet. A
handback is on a SENT purchase order, so `trade` must be passed explicitly or
it answers about whatever drafts happen to sit on the job instead. The PO name
carries the trade category ("Roofing Contractor"), which is what we send.

Fails open throughout. A handback email with no suggestions is still useful; a
handback email that never arrives because the suggester was cold is not.
"""
import json
import os

import boto3

SUGGEST_FUNCTION = os.environ.get("SUGGEST_FUNCTION", "abcg-trade-suggest")
SUGGEST_ENABLED = os.environ.get("SUGGEST_ENABLED", "true") == "true"

_lam = None


def _lambda():
    global _lam
    if _lam is None:
        _lam = boto3.client("lambda")
    return _lam


def suggest_for(job, exclude_vendor_id=None, exclude_vendor_name=None):
    """
    Returns (picks, context).

      picks   [{"name","why","phone","email","detail"}]
      context {"zone":..., "vendors_in_zone":N, "trade":..., "matched":bool}
    """
    if not SUGGEST_ENABLED or not job.get("claim_id"):
        return [], {}

    trade = (job.get("scope") or "").strip()
    payload = {"job_id": job["claim_id"]}
    if trade:
        payload["trade"] = trade

    try:
        resp = _lambda().invoke(
            FunctionName=SUGGEST_FUNCTION,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
        body = json.loads(resp["Payload"].read() or "{}")
        if isinstance(body, dict) and "body" in body and "breakdown" not in body:
            body = json.loads(body["body"])
    except Exception as e:  # noqa: BLE001
        print("TRADE SUGGEST unavailable (non-fatal): %s" % e)
        return [], {}

    picks, ctx = _parse(body, trade, exclude_vendor_name)
    return picks, ctx


def _parse(body, wanted_trade, exclude_name):
    if not isinstance(body, dict):
        return [], {}

    if "breakdown" in body:
        return _parse_breakdown(body, wanted_trade, exclude_name)
    return _parse_trade(body, exclude_name)


def _parse_trade(body, exclude_name):
    """The `trade` shape: three ranked lists, richest answer available."""
    context = {
        "trade": body.get("trade"),
        "zone": body.get("zone"),
        "vendors_in_zone": body.get("vendors_in_zone"),
        "vendors_in_trade": body.get("vendors_in_trade"),
        "median_days": body.get("trade_median_days"),
        "matched": True,
    }

    picks = []
    seen = set()
    # One from each list, so the PM sees a spread rather than three variations
    # of the same recommendation. Fastest is often empty - skip it silently.
    for key, label in (("best_value_with_capacity", "best value, has capacity"),
                       ("fastest_with_capacity", "fastest, has capacity"),
                       ("worth_trying", "worth trying")):
        for v in (body.get(key) or []):
            if not isinstance(v, dict):
                continue
            name = v.get("vendor")
            if not name or name in seen:
                continue
            if exclude_name and name.strip() == exclude_name.strip():
                continue
            seen.add(name)
            picks.append({"name": name, "why": label,
                          "phone": v.get("phone"), "email": v.get("email"),
                          "detail": _detail(v)})
            break          # one per list
    return picks, context


def _parse_breakdown(body, wanted_trade, exclude_name):
    """The no-`trade` shape: single objects per bucket, drafts only."""
    blocks = body.get("breakdown") or []
    if not blocks:
        return [], {}

    want = (wanted_trade or "").lower()
    block = next((b for b in blocks if (b.get("trade") or "").lower() == want),
                 blocks[0])
    context = {
        "trade": block.get("trade"),
        "zone": block.get("zone"),
        "vendors_in_zone": block.get("vendors_in_zone"),
        "matched": (block.get("trade") or "").lower() == want,
    }

    picks = []
    for key, label in (("best_value", "best value"),
                       ("fastest", "fastest with capacity"),
                       ("worth_trying", "worth trying")):
        v = block.get(key)
        if not isinstance(v, dict):
            continue
        name = v.get("vendor")
        if not name or (exclude_name and name.strip() == exclude_name.strip()):
            continue
        picks.append({"name": name, "why": label,
                      "phone": v.get("phone"), "email": v.get("email"),
                      "detail": _detail(v)})
    return picks, context


def _detail(v):
    """A short, honest line. Only what the suggester actually returned."""
    bits = []
    if v.get("open_jobs_30d") is not None:
        n = v["open_jobs_30d"]
        bits.append("no open jobs" if n == 0 else
                    "%d open job%s in 30 days" % (n, "s" if n != 1 else ""))
    if v.get("pos_this_trade_12m"):
        bits.append("%d POs this trade in 12 months" % v["pos_this_trade_12m"])
    if v.get("cost_vs_peers") is not None:
        c = v["cost_vs_peers"]
        bits.append("%d%% below peer cost" % (100 - c) if c < 100 else
                    "%d%% above peer cost" % (c - 100) if c > 100 else
                    "at peer cost")
    if v.get("resend_pct") is not None:
        bits.append("%s%% resend rate" % v["resend_pct"])
    if v.get("reconciles_at") is not None:
        bits.append("reconciles at %s%% of PO value"
                    % int(float(v["reconciles_at"]) * 100))
    if v.get("history"):
        bits.append(v["history"])
    return " &middot; ".join(bits)

