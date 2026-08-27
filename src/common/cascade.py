"""
Trade sequence dependency engine.

The point of this module is restraint. A move only matters if it consumes more
float than the successor has. Everything else is absorbed by the schedule and the
downstream trade never hears about it.

Terms:
  activity  - one trade's booked window on one claim job. Usually one PO.
  successor - an activity that cannot start until this one finishes.
  lag       - working days required between predecessor finish and successor start
              (cure time, dry-out, inspection window).
  float     - working days a successor can slip before it breaks something.
"""
from datetime import date, timedelta

# Insurance repair work runs Mon-Sat on most sites. Adjust per builder if needed.
WORKING_DAYS = {0, 1, 2, 3, 4, 5}  # Monday=0 ... Saturday=5


def _is_working(d):
    return d.weekday() in WORKING_DAYS


def add_working_days(d, n):
    if n == 0:
        while not _is_working(d):
            d += timedelta(days=1)
        return d
    step = 1 if n > 0 else -1
    remaining = abs(n)
    while remaining:
        d += timedelta(days=step)
        if _is_working(d):
            remaining -= 1
    return d


def working_days_between(a, b):
    """Signed count of working days from a to b."""
    if a == b:
        return 0
    step = 1 if b > a else -1
    cur, n = a, 0
    while cur != b:
        cur += timedelta(days=step)
        if _is_working(cur):
            n += step
    return n


def _d(v):
    return v if isinstance(v, date) else date.fromisoformat(v)


def earliest_start(activity, by_id, finishes):
    """Earliest this activity can begin given where its predecessors now finish."""
    preds = activity.get("predecessors") or []
    if not preds:
        return _d(activity["start"])
    starts = []
    for pid in preds:
        p = by_id.get(pid)
        if not p:
            continue
        lag = int(p.get("lag_days", activity.get("lag_days", 0)))
        starts.append(add_working_days(finishes[pid], lag + 1))
    return max(starts) if starts else _d(activity["start"])


def simulate(activities, moved_id, new_start):
    """
    Forward pass over one claim job's trade sequence with one activity moved.

    activities: [{id, po, activity, vendor_id, vendor_name, start, finish,
                  predecessors:[id], lag_days:int, locked:bool}]

    Returns impacts only for successors whose float is exhausted. An activity that
    absorbs the slip is reported as absorbed and nobody is contacted about it.
    """
    by_id = {a["id"]: a for a in activities}
    order = _topo(activities)
    new_start = _d(new_start)

    finishes, results = {}, []

    for aid in order:
        a = by_id[aid]
        booked_start, booked_finish = _d(a["start"]), _d(a["finish"])
        duration = max(working_days_between(booked_start, booked_finish), 0)

        if aid == moved_id:
            start = new_start
        else:
            est = earliest_start(a, by_id, finishes)
            start = max(booked_start, est)

        finish = add_working_days(start, duration)
        finishes[aid] = finish

        slip = working_days_between(booked_start, start)
        results.append({
            "id": aid,
            "po": a.get("po"),
            "activity": a.get("activity"),
            "vendor_id": a.get("vendor_id"),
            "vendor_name": a.get("vendor_name"),
            "booked_start": booked_start.isoformat(),
            "booked_finish": booked_finish.isoformat(),
            "new_start": start.isoformat(),
            "new_finish": finish.isoformat(),
            "slip_days": slip,
            "is_source": aid == moved_id,
            "locked": bool(a.get("locked")),
        })

    impacted = [r for r in results if r["slip_days"] > 0 and not r["is_source"]]
    absorbed = [r for r in results
                if r["slip_days"] == 0 and not r["is_source"] and _downstream(r["id"], by_id, moved_id)]
    clashes = [r for r in impacted if r["locked"]]

    baseline = max((_d(a["finish"]) for a in activities), default=None)
    revised = max(finishes.values(), default=None)

    return {
        "source_id": moved_id,
        "impacted": impacted,
        "absorbed": absorbed,
        "locked_clashes": clashes,
        "practical_completion": {
            "was": baseline.isoformat() if baseline else None,
            "now": revised.isoformat() if revised else None,
            "slip_days": working_days_between(baseline, revised) if baseline and revised else 0,
        },
    }


def float_days(activities):
    """Working days each activity can slip before it pushes a successor. Diagnostic."""
    by_id = {a["id"]: a for a in activities}
    succs = {a["id"]: [] for a in activities}
    for a in activities:
        for p in (a.get("predecessors") or []):
            if p in succs:
                succs[p].append(a["id"])
    out = {}
    for a in activities:
        gaps = []
        for sid in succs[a["id"]]:
            s = by_id[sid]
            lag = int(a.get("lag_days", 0))
            need = add_working_days(_d(a["finish"]), lag + 1)
            gaps.append(working_days_between(need, _d(s["start"])))
        out[a["id"]] = min(gaps) if gaps else None  # None = nothing downstream
    return out


def _downstream(aid, by_id, source_id):
    seen, stack = set(), [source_id]
    while stack:
        cur = stack.pop()
        for a in by_id.values():
            if cur in (a.get("predecessors") or []) and a["id"] not in seen:
                seen.add(a["id"])
                stack.append(a["id"])
    return aid in seen


def _topo(activities):
    by_id = {a["id"]: a for a in activities}
    indeg = {a["id"]: len([p for p in (a.get("predecessors") or []) if p in by_id])
             for a in activities}
    ready = sorted([i for i, d in indeg.items() if d == 0],
                   key=lambda i: by_id[i]["start"])
    order = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for a in activities:
            if cur in (a.get("predecessors") or []):
                indeg[a["id"]] -= 1
                if indeg[a["id"]] == 0:
                    ready.append(a["id"])
        ready.sort(key=lambda i: by_id[i]["start"])
    if len(order) != len(activities):          # cycle in the sequence data
        order += [a["id"] for a in activities if a["id"] not in order]
    return order
