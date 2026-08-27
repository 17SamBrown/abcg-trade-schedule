"""
Email bodies. Pure functions: data in, (subject, html) out. No AWS, no I/O.
That is what lets you render a full week locally and read it before anything sends.

ABCG palette per the brand guide. Arial, white background, teal headers.
"""
from datetime import date

TEAL = "#00aa86"
GREEN = "#55a51c"
MIDBLUE = "#0078c9"
CYAN = "#00b1c1"
DARK = "#2a2723"
GREY = "#939598"
RULE = "#e4e4e2"
ALERT = "#c0392b"
ALERTWASH = "#fdf1ef"
TEALWASH = "#e6f6f3"

# Crunchwork job link, same pattern as the Daily PM Update
PULSE_JOB_URL = "https://abcg.unitycloud.io/pulse/abcg/jobs/{claim_id}/details"

LABEL = {
    "handback": "CANNOT DO THIS JOB",
    "confirmed": "Dates confirmed",
    "underway": "On site now",
    "completed": "Reported complete",
    "move": "Date change requested",
}
COLOUR = {
    "handback": ALERT,
    "confirmed": GREEN,
    "underway": MIDBLUE,
    "completed": CYAN,
    "move": ALERT,
}


def _d(v):
    return v if isinstance(v, date) else date.fromisoformat(v)


def nice(v):
    return _d(v).strftime("%a %-d %b") if v else ""


def _shell(heading, subheading, inner):
    return f"""<html><body style="margin:0;padding:0;background:#fff;
font-family:Arial,Helvetica,sans-serif;color:{DARK}">
<div style="max-width:640px;margin:0 auto">
  <div style="background:{TEAL};color:#fff;padding:18px 20px">
    <div style="font-size:12px;letter-spacing:.15em;text-transform:uppercase">{heading}</div>
    <div style="font-size:21px;font-weight:bold;margin-top:5px">{subheading}</div>
  </div>
  <div style="padding:20px">{inner}</div>
</div></body></html>"""


# --------------------------------------------------------------------------
# 1. Weekly invite to the trade
# --------------------------------------------------------------------------

def trade_invite(vendor, jobs, link, carried=None):
    """jobs = what needs an answer. carried = already answered, shown for context only."""
    carried = carried or []
    n = len(jobs)
    overdue = [j for j in jobs if j.get("band") == "overdue"]

    if n == 0:
        subject = f"ABCG: your schedule is up to date ({len(carried)} jobs)"
    else:
        subject = f"ABCG: {n} job{'s' if n != 1 else ''} need{'' if n != 1 else 's'} your answer"
        if overdue:
            subject += f" ({len(overdue)} past its finish date)"

    rows = "".join(f"""<tr><td style="padding:10px 14px;border-bottom:1px solid {RULE}">
          <div style="font-weight:bold">{j['addr']}</div>
          <div style="font-size:13px;color:{GREY}">PO {j['po']}{" &middot; job " + j['job_ref'] if j.get('job_ref') else ""} &middot; {j.get('scope','')}</div>
          <div style="font-size:13px;margin-top:3px">{nice(j['start'])} to {nice(j['finish'])}</div>
        </td></tr>""" for j in sorted(jobs, key=lambda x: x.get("start") or ""))

    table = ""
    if rows:
        table = (f"<table style=\"width:100%;border-collapse:collapse;"
                 f"border:1px solid {RULE}\">" + rows + "</table>")

    carried_line = ""
    if carried:
        carried_line = (f"<p style='color:{GREY};font-size:13px;margin-top:16px'>"
                        f"Your other {len(carried)} job{'s are' if len(carried) != 1 else ' is'} "
                        f"already answered and unchanged. {'They are' if len(carried) != 1 else 'It is'} "
                        f"on the form if you need to change {'them' if len(carried) != 1 else 'it'}.</p>")

    if n == 0:
        lead = ("<p>Nothing new to confirm this week. Everything ABCG has booked with you "
                "is already answered.</p>")
        cta = "Review my schedule"
    else:
        lead = (f"<p><b>{n} job{'s' if n != 1 else ''}</b> need"
                f"{'' if n != 1 else 's'} an answer. Confirm the dates, tell us it is underway "
                f"or done, or ask to move it. About a minute.</p>")
        cta = f"Answer {n} job{'s' if n != 1 else ''}"

    inner = f"""
    <p>Hi {vendor.get('contact') or vendor['name']},</p>
    {lead}
    <p style="text-align:center;margin:26px 0">
      <a href="{link}" style="background:{TEAL};color:#fff;text-decoration:none;padding:15px 32px;
      border-radius:5px;font-weight:bold;display:inline-block">{cta}</a></p>
    {table}{carried_line}
    <p style="color:{GREY};font-size:13px;margin-top:20px">
      This link is personal to {vendor['name']} and expires in 21 days. If we do not hear back,
      your project manager will call.</p>"""
    return subject, _shell("ABCG Trade Schedule", "Confirm your schedule", inner)


# --------------------------------------------------------------------------
# 2. Response digest to the PM (RC on CC)
# --------------------------------------------------------------------------

def pm_digest(pm_name, vendor_name, rows, impacts=None, options=None):
    """
    rows:    [(job dict, response dict)] - only this PM's jobs
    impacts: {job_id: cascade.simulate(...) result} for move responses
    """
    impacts = impacts or {}
    options = options or {}
    handbacks = [(j, r) for j, r in rows if r["state"] == "handback"]
    moves = [r for _, r in rows if r["state"] == "move"]
    completed = [r for _, r in rows if r["state"] == "completed"]
    breaks = sum(len(impacts.get(j["job_id"], {}).get("impacted", [])) for j, _ in rows)

    if handbacks:
        n = len(handbacks)
        subject = (f"ACTION: {vendor_name} has handed back "
                   f"{n} job{'s' if n != 1 else ''} - reallocation needed")
    elif moves:
        subject = f"{vendor_name}: {len(moves)} date change request{'s' if len(moves) != 1 else ''}"
        if breaks:
            subject += f", {breaks} trade{'s' if breaks != 1 else ''} affected"
    else:
        subject = f"{vendor_name}: schedule confirmed ({len(rows)} job{'s' if len(rows) != 1 else ''})"

    banner = ""
    if handbacks:
        lines = "".join(
            f"<li style='margin-bottom:4px'><b>{_link(j)}</b> (PO {j['po']}) "
            f"&mdash; {r['reason']}</li>" for j, r in handbacks)
        banner += (f"<div style='background:{ALERTWASH};border-left:4px solid {ALERT};"
                   f"padding:14px 16px;margin:0 0 16px'>"
                   f"<b style='color:{ALERT}'>Reallocation needed.</b> "
                   f"{vendor_name} cannot do the following. These jobs currently have "
                   f"nobody allocated to them.<ul style='margin:8px 0 0;padding-left:18px'>"
                   f"{lines}</ul></div>")
    if moves:
        banner += (f"<p style='background:{ALERTWASH};border-left:4px solid {ALERT};"
                   f"padding:12px 14px;margin:0 0 16px'><b>Action needed.</b> Nothing has moved "
                   f"in the scheduler. These dates change only when you accept them.</p>")
    if completed:
        banner += (f"<p style='background:{TEALWASH};border-left:4px solid {CYAN};"
                   f"padding:12px 14px;margin:0 0 16px'><b>{len(completed)} reported complete.</b> "
                   f"Verify against site photos or the invoice before closing.</p>")

    body = "".join(
        _job_block(j, r, impacts.get(j["job_id"]), options.get(j["job_id"]))
        for j, r in sorted(rows, key=lambda x: x[0].get("start") or "")
    )

    first = pm_name.split()[0] if pm_name else "there"
    inner = f"""<p>Hi {first},</p>{banner}{body}
    <p style="color:{GREY};font-size:13px;margin-top:20px">
      Sent when {vendor_name} submitted their weekly confirmation. Only your jobs are shown.</p>"""
    return subject, _shell("Trade Schedule Response", vendor_name, inner)


def _job_block(j, r, impact, picks=None):
    state = r["state"]
    detail = ""
    if state == "handback":
        detail = (f"<div style='margin-top:8px;font-size:14px'>"
                  f"Reason: <b>{r['reason']}</b></div>")
        if r.get("note"):
            detail += (f"<div style='margin-top:4px;font-size:13px;color:{GREY}'>"
                       f"&ldquo;{r['note']}&rdquo;</div>")
        detail += (f"<div style='margin-top:8px;font-size:13px;color:{ALERT}'>"
                   f"<b>Nobody is allocated to this job.</b></div>")
    if state == "move":
        detail = (f"<div style='margin-top:8px;font-size:14px'>Wants "
                  f"<b>{nice(r['new_start'])}</b> &middot; reason: {r['reason']}</div>")
        if r.get("note"):
            detail += (f"<div style='margin-top:4px;font-size:13px;color:{GREY}'>"
                       f"&ldquo;{r['note']}&rdquo;</div>")

    cascade_html = _cascade_block(impact) if impact else ""
    if state == "handback" and picks:
        cascade_html += _suggest_block(picks)

    return f"""<div style="border:1px solid {RULE};border-left:4px solid {COLOUR[state]};
      margin-bottom:14px">
      <div style="padding:12px 14px">
        <div style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;
          color:{COLOUR[state]};font-weight:bold">{LABEL[state]}</div>
        <div style="font-weight:bold;font-size:17px;margin-top:3px">{_link(j)}</div>
        <div style="font-size:13px;color:{GREY}">PO {j['po']}{" &middot; job " + j['job_ref'] if j.get('job_ref') else ""}
          &middot; booked {nice(j['start'])} to {nice(j['finish'])}</div>
        {detail}
      </div>{cascade_html}</div>"""


def _link(j):
    """Address as a link straight into the Pulse job. Falls back to plain text."""
    claim = j.get("claim_id")
    if not claim:
        return j.get("addr") or ""
    url = PULSE_JOB_URL.format(claim_id=claim)
    parts = [p for p in (j.get("job_type"), j.get("job_ref"),
                         ", ".join(x for x in (j.get("addr"), j.get("suburb")) if x))
             if p]
    text = " | ".join(parts) or "Open job"
    return (f'<a href="{url}" style="color:{TEAL};text-decoration:none;'
            f'border-bottom:1px solid {TEAL}">{text}</a>')


def _suggest_block(opt):
    """
    Replacement trades from the scorecard. Ranked, not chosen - reallocation is
    a commercial decision and an email is not the place to make it.
    """
    picks = (opt or {}).get("picks") or []
    ctx = (opt or {}).get("context") or {}
    if not picks:
        return ""

    rows = ""
    for p in picks:
        contact = " &middot; ".join(x for x in (p.get("phone"), p.get("email")) if x)
        rows += f"""<tr><td style="padding:8px 0;font-size:13px;
          border-bottom:1px solid {RULE}">
          <b>{p['name']}</b>
          <span style="color:#008065;font-size:11px;letter-spacing:.06em;
            text-transform:uppercase"> &middot; {p['why']}</span>
          {f'<div style="color:{GREY};margin-top:2px">{p["detail"]}</div>' if p.get("detail") else ""}
          {f'<div style="margin-top:2px">{contact}</div>' if contact else ""}
        </td></tr>"""

    head = "Trades who could pick this up"
    if ctx.get("trade") and not ctx.get("matched"):
        head += f" (nearest match: {ctx['trade']})"

    foot = "Ranked from the vendor scorecard. Compliance and zone checks applied."
    if ctx.get("zone"):
        foot = f"{ctx['zone']}"
        if ctx.get("vendors_in_zone"):
            foot += f" &middot; {ctx['vendors_in_zone']} vendors in zone"

    return f"""<div style="padding:12px 14px;background:{TEALWASH};
      border-top:1px solid {RULE}">
      <div style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;
        color:#008065;font-weight:bold;margin-bottom:6px">{head}</div>
      <table style="width:100%;border-collapse:collapse">{rows}</table>
      <div style="margin-top:8px;font-size:12px;color:{GREY}">{foot}</div>
    </div>"""


def _cascade_block(impact):
    hit = impact.get("impacted") or []
    absorbed = impact.get("absorbed") or []
    pc = impact.get("practical_completion") or {}

    if not hit:
        return (f"<div style='padding:10px 14px;background:{TEALWASH};border-top:1px solid {RULE};"
                f"font-size:13px'><b>No downstream impact.</b> "
                f"{len(absorbed)} following trade{'s' if len(absorbed) != 1 else ''} "
                f"absorb{'' if len(absorbed) != 1 else 's'} this.</div>")

    lines = "".join(
        f"""<tr><td style="padding:6px 0;font-size:13px">
          <b>{i['vendor_name']}</b> &middot; {i['activity']} (PO {i['po']})<br>
          <span style="color:{GREY}">{nice(i['booked_start'])} &rarr; {nice(i['new_start'])},
          {i['slip_days']} working day{'s' if i['slip_days'] != 1 else ''}</span></td></tr>"""
        for i in hit
    )
    pc_line = ""
    if pc.get("slip_days"):
        pc_line = (f"<div style='margin-top:8px;font-size:13px;color:{ALERT}'>"
                   f"<b>Practical completion slips {pc['slip_days']} working day"
                   f"{'s' if pc['slip_days'] != 1 else ''}</b>, {nice(pc['was'])} "
                   f"&rarr; {nice(pc['now'])}.</div>")
    else:
        pc_line = (f"<div style='margin-top:8px;font-size:13px;color:{GREY}'>"
                   f"Practical completion holds at {nice(pc.get('now'))}.</div>")

    absorbed_line = ""
    if absorbed:
        names = ", ".join(sorted({a["vendor_name"] for a in absorbed}))
        absorbed_line = (f"<div style='margin-top:6px;font-size:13px;color:{GREY}'>"
                         f"Absorbed, not contacted: {names}.</div>")

    return f"""<div style="padding:12px 14px;background:{ALERTWASH};border-top:1px solid {RULE}">
      <div style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:{ALERT};
        font-weight:bold;margin-bottom:6px">If you accept, {len(hit)} trade{'s' if len(hit) != 1 else ''} move{'' if len(hit) != 1 else 's'}</div>
      <table style="width:100%;border-collapse:collapse">{lines}</table>
      {pc_line}{absorbed_line}</div>"""


