"""
Crunchwork reads for the trade schedule build.

Three databases, three queries, joined in Python because Postgres cannot join
across databases on the same instance.

  purchase_orders   the POs themselves, plus their status lookup. The site
                    address lives on the PO in the to_* fields, so no join to
                    pulse_2 is needed just to know where the work is.
  vendor_manager    vendor name, email, phone
  pulse_2           the job, only for project manager, supervisor and repair
                    coordinator. UNCONFIRMED - see JOB_ROLES below.

Read only. No INSERT, UPDATE or DELETE anywhere in this file.
"""
from common import db

# ---------------------------------------------------------------------------
# CONFIRMED from the Power BI dataflow and the trade-suggest build
# ---------------------------------------------------------------------------
PO_DB = "purchase_orders"
VENDOR_DB = "vendor_manager"
JOB_DB = "pulse_2"

# PO status names treated as open. Run the discovery Lambda to list the real
# values in purchase_orders.public.statuses and correct these.
# CONFIRMED from discovery: only five PO statuses exist. Reconciled (100k) is
# finished work, Draft (27k) is not issued, Cancelled (24k) is dead, Pending
# Approval (106) is not yet a commitment. Sent (5,340) is the live set.
OPEN_STATUS_NAMES = ["Sent"]

# ---------------------------------------------------------------------------
# JOB statuses that must NEVER receive a schedule confirmation, regardless of
# whether the PO underneath is still open.
#
# A cash settled or cancelled job with a stale open PO is exactly the case that
# sends a trade to a site where there is no work. This filter is the guard, and
# it FAILS CLOSED: if the job status cannot be read, the dispatch aborts rather
# than sending. Compare _fetch_job_roles, which fails open because the worst
# case there is a digest landing in the wrong inbox.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Job statuses that never receive a schedule confirmation.
#
# Written with plain hyphens deliberately. The source data mixes en dashes and
# hyphens ("Repairs Stalled – Maintenance" vs "Repairs Stalled - Restoration")
# and one entry has no space before its hyphen, so both sides are normalised
# before comparison. Exact matching here would silently let stalled work
# through, which is the failure this list exists to prevent.
# ---------------------------------------------------------------------------
EXCLUDED_JOB_STATUSES = [
    # finished, dead, or settled
    "Awaiting Payment",
    "Cancelled",
    "Closed",
    "Cash Settled",
    # not approved, so ABCG has not committed to the work
    "Not Approved",
    "Pending Approval",
    # waiting on someone outside ABCG - dates are not real yet
    "Awaiting Customer",
    "Awaiting Excess / Scope / Contract",
    "Awaiting Insurer - Cash Settlement Pending",
    # stalled
    "Repairs Stalled - Maintenance",
    "Repairs Stalled - Customer",
]


def _norm_status(v):
    """Fold en/em dashes to hyphens, collapse whitespace, lowercase."""
    if not v:
        return ""
    s = str(v).replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("-", " - ")
    return " ".join(s.split()).lower()


_EXCLUDED_NORM = {_norm_status(x) for x in EXCLUDED_JOB_STATUSES}


# ---------------------------------------------------------------------------
# Only these job types get a schedule confirmation. Rectification work is
# excluded: it is remedial, it is scheduled differently, and asking a trade to
# confirm dates on a rectification they may be disputing is the wrong
# conversation entirely.
#
# Same fail-closed rule as job status. If the type cannot be read, nothing sends.
# ---------------------------------------------------------------------------
ALLOWED_JOB_TYPES = [
    "Works",
    "IAG Completion",
]

# ---------------------------------------------------------------------------
# Who hears about a response.
#
# pulse_2.public.assignees holds job_id -> user_id + role. Anyone can hold any
# role on any job, so the role string is authoritative and the person's usual
# title is irrelevant. Names and emails come from the separate `core` database,
# table "Users" (quoted - it is capitalised), so this is another Python join.
#
# PRIMARY is who the digest is addressed to. COPIED are CC'd. If no primary is
# assigned we walk FALLBACK in order rather than dropping the job silently.
# ---------------------------------------------------------------------------
PRIMARY_ROLE = "Project Manager"
COPIED_ROLES = ["Supervisor", "Repair Coordinator"]
FALLBACK_ROLES = ["Supervisor", "Repair Coordinator", "Manager"]
USER_DB = "core"

# CONFIRMED from core."Users": camelCase identifiers, so everything needs
# quoting. There is no single name column - firstName and lastName are joined.
# Departed and disabled staff are filtered out here, which means a job whose
# only Project Manager has left falls through to the fallback chain instead of
# emailing someone who no longer works at ABCG.


def fetch_job_assignees(claim_ids):
    """
    {job_id: {role: [{"name":..., "email":...}, ...]}}

    Several people can hold one role on one job. All are returned and all are
    contacted: a duplicate email is a smaller failure than the right person
    never being told.
    """
    if not claim_ids:
        return {}
    wanted = [PRIMARY_ROLE] + COPIED_ROLES + FALLBACK_ROLES

    rows = db.query("""
        SELECT job_id, user_id, role
        FROM public.assignees
        WHERE job_id = ANY(:ids)
          AND deleted_at IS NULL
          AND role = ANY(:roles)
    """, database=JOB_DB, ids=[str(c) for c in claim_ids], roles=wanted)
    if not rows:
        return {}

    user_ids = sorted({str(r["user_id"]) for r in rows if r.get("user_id")})
    users = {}
    try:
        for x in db.query("""
            SELECT "id" AS id,
                   NULLIF(TRIM(CONCAT_WS(' ', "firstName", "lastName")), '') AS name,
                   "email" AS email
            FROM public."Users"
            WHERE "id"::text = ANY(:ids)
              AND "deletedAt" IS NULL
              AND COALESCE(disabled, false) = false
        """, database=USER_DB, ids=user_ids):
            users[str(x["id"])] = x
    except Exception as e:  # noqa: BLE001
        # Fails open. Worst case a digest routes to the test recipient - not
        # worth blocking a whole run over a name lookup.
        print("USER LOOKUP FAILED: %s" % e)
        return {}

    out = {}
    for r in rows:
        person = users.get(str(r.get("user_id")))
        if not person or not person.get("email"):
            continue
        out.setdefault(str(r["job_id"]), {}).setdefault(r["role"], []).append(
            {"name": person.get("name"), "email": person["email"]})
    return out


def recipients_for(assignees_for_job):
    """
    (primary, copied, how_resolved).

    A job with nobody in any of these roles returns empty lists and "none",
    which the caller reports rather than silently swallows.
    """
    a = assignees_for_job or {}
    primary = a.get(PRIMARY_ROLE) or []
    resolved = PRIMARY_ROLE

    if not primary:
        for role in FALLBACK_ROLES:
            if a.get(role):
                primary = a[role]
                resolved = "%s (no %s assigned)" % (role, PRIMARY_ROLE)
                break

    copied, seen = [], {p["email"] for p in primary}
    for role in COPIED_ROLES:
        for p in (a.get(role) or []):
            if p["email"] not in seen:
                seen.add(p["email"])
                copied.append(p)

    if not primary:
        return [], [], "none"
    return primary, copied, resolved



class JobStatusUnavailable(RuntimeError):
    """Raised when job status cannot be confirmed. Dispatch must not proceed."""


# CONFIRMED: jobs.status is a plain text column, not a lookup table.
# jobs.job_type_id is a FK - the lookup table name is UNCONFIRMED, correct it
# from the discovery output.
JOB_STATUS = {"table": "jobs", "id": "id", "status": "status",
              "type_fk": "job_type_id", "type_table": "job_types",
              "type_name": "name"}

# ---------------------------------------------------------------------------
# UNCONFIRMED. pulse_2.jobs almost certainly does not hold PM, supervisor and
# repair coordinator as plain columns - they are more likely rows in
# pulse_2.assignments keyed by a role. Run the discovery script before relying
# on this, and correct here.
# ---------------------------------------------------------------------------
JOB_ROLES = {
    "table": "jobs",
    "id": "id",
    "pm": "project_manager_id",
    "supervisor": "supervisor_id",
    "rc": "repair_coordinator_id",
}


def _open_pos_sql(extra=""):
    return f"""
    SELECT
        po.id                       AS po_id,
        po.purchase_order_number    AS po_number,
        po.name                     AS po_name,
        po.date                     AS start_date,
        po.end_date                 AS finish_date,
        po.created_at               AS raised,
        po.vendor_id                AS vendor_id,
        po.pulse_job_id             AS claim_id,
        po.parent_po_id             AS parent_po_id,
        po.to_unit_number           AS to_unit,
        po.to_street_number         AS to_street_no,
        po.to_street_name           AS to_street,
        po.to_suburb                AS to_suburb,
        po.to_state                 AS to_state,
        po.to_post_code             AS to_postcode,
        s.name                      AS status_name
    FROM public.purchase_orders po
    JOIN public.statuses s ON s.id = po.status_id
    WHERE po.deleted_at IS NULL
      AND s.name = ANY(:statuses)
      {extra}
    """


def fetch_open_pos():
    """Baseline. Every open PO regardless of date, including undated ones."""
    rows = db.query(_open_pos_sql(), database=PO_DB, statuses=OPEN_STATUS_NAMES)
    return _enrich(rows)


def fetch_scheduled_pos(horizon_days=28, overdue_days=21):
    """Rolling. Windows opening inside the horizon, plus anything overdue."""
    extra = """
      AND (
            po.date BETWEEN CURRENT_DATE - (:overdue * INTERVAL '1 day')
                        AND CURRENT_DATE + (:horizon * INTERVAL '1 day')
         OR (po.end_date < CURRENT_DATE
             AND po.end_date >= CURRENT_DATE - (:overdue * INTERVAL '1 day'))
          )
    """
    rows = db.query(_open_pos_sql(extra), database=PO_DB,
                    statuses=OPEN_STATUS_NAMES,
                    horizon=horizon_days, overdue=overdue_days)
    return _enrich(rows)


def fetch_claim_sequences(claim_ids):
    """
    Every open PO on the given claims, so the cascade runs later without a
    second database read. Snapshotted into DynamoDB at token-issue time.
    """
    if not claim_ids:
        return {}
    rows = db.query(_open_pos_sql("AND po.pulse_job_id = ANY(:claims)"),
                    database=PO_DB, statuses=OPEN_STATUS_NAMES,
                    claims=[str(c) for c in claim_ids])
    shaped = _enrich(rows)

    out = {}
    for s in shaped:
        out.setdefault(s["claim_id"], []).append({
            "id": s["job_id"], "po": s["po"], "activity": s["scope"],
            "vendor_id": s["vendor_id"], "vendor_name": s["vendor_name"],
            "start": s["start"], "finish": s["finish"],
            "predecessors": [], "lag_days": 0,
        })

    # No dependency column exists on purchase_orders, so sequence by booked
    # start date: each activity depends on the one before it. Correct for a
    # linear trade run, wrong for anything parallel. Advisory only - the PM
    # sees the cascade before accepting anything.
    for acts in out.values():
        ordered = sorted([a for a in acts if a["start"]], key=lambda a: a["start"])
        for prev, nxt in zip(ordered, ordered[1:]):
            nxt["predecessors"] = [prev["id"]]
    return out


# ---------------------------------------------------------------------------

def _enrich(rows):
    """Attach vendor and job-role detail from the other two databases."""
    if not rows:
        return []

    vendor_ids = sorted({str(r["vendor_id"]) for r in rows if r.get("vendor_id")})
    vendors = {}
    if vendor_ids:
        for v in db.query("""
            SELECT id, business_name, email, phone
            FROM public.vendors
            WHERE id = ANY(:ids) AND deleted_at IS NULL
        """, database=VENDOR_DB, ids=vendor_ids):
            vendors[str(v["id"])] = v

    claim_ids = sorted({str(r["claim_id"]) for r in rows if r.get("claim_id")})

    # Fails closed. If this raises, nothing is sent.
    excluded = _fetch_excluded_claims(claim_ids)
    if excluded:
        print(f"EXCLUDED {len(excluded)} claims on a non-schedulable job status")
    rows = [r for r in rows if str(r.get("claim_id")) not in excluded]

    # A PO with no job attached cannot have its job status checked, so it is
    # excluded too. Same reasoning: unverifiable means do not send.
    orphans = [r for r in rows if not r.get("claim_id")]
    if orphans:
        print(f"EXCLUDED {len(orphans)} POs with no job attached")
        rows = [r for r in rows if r.get("claim_id")]

    jobs = {}
    return [_shape(r, vendors, jobs) for r in rows]


_JOB_DETAIL = {}


def _fetch_excluded_claims(claim_ids):
    """
    Claim ids whose job status means no schedule confirmation should be sent.

    Raises JobStatusUnavailable on any failure. Never returns an empty set to
    mean "could not check" - that would silently send confirmations for
    cancelled and cash settled work.
    """
    if not claim_ids:
        return set()
    c = JOB_STATUS
    try:
        rows = db.query(f"""
            SELECT j.{c['id']}   AS job_id,
                   j.{c['status']} AS status,
                   j.reference   AS job_ref,
                   t.name        AS job_type,
                   a.full_address AS full_address,
                   a.unit_number  AS unit_number,
                   a.street_number AS street_number,
                   a.street_name  AS street_name,
                   a.city         AS city,
                   a.state        AS state,
                   a.postcode     AS postcode
            FROM public.{c['table']} j
            LEFT JOIN public.job_types t ON t.id = j.job_type_id
            LEFT JOIN public.addresses a
                   ON a.id = j.address_id AND a.deleted_at IS NULL
            WHERE j.{c['id']} = ANY(:ids) AND j.deleted_at IS NULL
        """, database=JOB_DB, ids=claim_ids)
    except Exception as e:  # noqa: BLE001
        raise JobStatusUnavailable(
            f"Could not read job status, so nothing can be sent safely. "
            f"Correct JOB_STATUS in pulse.py. Underlying error: {e}") from e

    seen = {str(r["job_id"]) for r in rows}
    missing = set(claim_ids) - seen
    if missing:
        # A job we cannot find is a job we cannot clear. Exclude it.
        print(f"EXCLUDED {len(missing)} claims not found in {JOB_DB}.jobs")

    # cache the job detail so _enrich can use the site address without a
    # second query - the PO's to_ fields are billing data and often blank
    _JOB_DETAIL.clear()
    for r in rows:
        _JOB_DETAIL[str(r["job_id"])] = r

    by_status = {str(r["job_id"]) for r in rows
                 if _norm_status(r.get("status")) in _EXCLUDED_NORM}

    # Allow-list, not a block-list. An unrecognised job type is excluded, so a
    # new type appearing in Crunchwork does not silently start receiving
    # confirmations before anyone has decided that it should.
    by_type = {str(r["job_id"]) for r in rows
               if (r.get("job_type") or "").strip() not in ALLOWED_JOB_TYPES}

    if by_status:
        print(f"EXCLUDED {len(by_status)} claims on a non-schedulable status")
    if by_type:
        print(f"EXCLUDED {len(by_type)} claims on a job type outside "
              f"{ALLOWED_JOB_TYPES}")
    return by_status | by_type | missing



def _addr(r):
    """
    Site address from the job. The PO's to_ fields are billing detail and are
    blank on a meaningful share of records, so they are only a fallback.
    """
    j = _JOB_DETAIL.get(str(r.get("claim_id"))) or {}
    if j.get("street_name"):
        unit = j.get("unit_number")
        num = " ".join(filter(None, [str(j.get("street_number") or ""),
                                     j.get("street_name") or ""])).strip()
        return f"{unit}/{num}" if unit else num
    if j.get("full_address"):
        return j["full_address"]
    unit = r.get("to_unit")
    num = " ".join(filter(None, [str(r.get("to_street_no") or ""),
                                 r.get("to_street") or ""])).strip()
    return f"{unit}/{num}" if unit else num


def _suburb(r):
    j = _JOB_DETAIL.get(str(r.get("claim_id"))) or {}
    if j.get("city"):
        return " ".join(filter(None, [j.get("city"), j.get("state"),
                                      str(j.get("postcode") or "")])).strip()
    return " ".join(filter(None, [r.get("to_suburb"), r.get("to_state"),
                                  str(r.get("to_postcode") or "")])).strip()


def _iso(v):
    return v.isoformat()[:10] if hasattr(v, "isoformat") else (v or None)


def _shape(r, vendors, jobs):
    v = vendors.get(str(r.get("vendor_id")), {})
    j = jobs.get(str(r.get("claim_id")), {})
    return {
        "job_id": str(r["po_id"]),
        "claim_id": str(r["claim_id"]) if r.get("claim_id") else None,
        "po": r.get("po_number") or r.get("po_name") or "",
        "scope": r.get("po_name") or "",
        "start": _iso(r.get("start_date")),
        "finish": _iso(r.get("finish_date")),
        "raised": _iso(r.get("raised")),
        "status": r.get("status_name"),
        "addr": _addr(r),
        "suburb": _suburb(r),
        "job_ref": (_JOB_DETAIL.get(str(r.get("claim_id"))) or {}).get("job_ref"),
        "job_type": (_JOB_DETAIL.get(str(r.get("claim_id"))) or {}).get("job_type"),
        "vendor_id": str(r["vendor_id"]) if r.get("vendor_id") else None,
        "vendor_name": v.get("business_name"),
        "vendor_contact": None,
        "vendor_email": v.get("email"),
        "vendor_mobile": v.get("phone"),
        "pm_id": str(j["pm_id"]) if j.get("pm_id") else None,
        "pm_name": j.get("pm_name"),
        "pm_email": j.get("pm_email"),
        "supervisor_name": j.get("supervisor_name"),
        "supervisor_email": j.get("supervisor_email"),
        "rc_name": j.get("rc_name"),
        "rc_email": j.get("rc_email"),
    }


def group_by_vendor(jobs):
    out = {}
    for j in jobs:
        if j.get("vendor_id"):
            out.setdefault(j["vendor_id"], []).append(j)
    for lst in out.values():
        lst.sort(key=lambda x: (x["start"] or "9999-99-99", x["po"] or ""))
    return out


def create_pm_task(*_a, **_k):
    raise NotImplementedError("Read replica. No write path exists.")


def create_activity_note(*_a, **_k):
    raise NotImplementedError("Read replica. No write path exists.")

