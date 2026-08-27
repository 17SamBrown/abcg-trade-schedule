"""
Run the full loop locally against tests/fixture_week.json. Sends nothing, writes
nothing to Crunchwork, needs no AWS credentials.

    python3 tools/local_run.py [outdir]

Produces:
    01_trade_invite.html          what the trade receives
    02_form_payload.json          what GET /v1/schedule returns
    03_pm_<name>.html             one per PM, RC on CC
    run_log.txt                   subjects, recipients, cascade summary
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from common import cascade, emails  # noqa: E402

HERE = os.path.dirname(__file__)
FIXTURE = os.path.join(HERE, "..", "tests", "fixture_week.json")


def main(outdir):
    os.makedirs(outdir, exist_ok=True)
    fx = json.load(open(FIXTURE))
    vendor = fx["vendor"]
    log = []

    # index every activity, and remember which claim job it belongs to
    claim_of, acts_of_claim, meta = {}, {}, {}
    for cj in fx["claim_jobs"]:
        acts_of_claim[cj["claim_id"]] = cj["activities"]
        meta[cj["claim_id"]] = cj
        for a in cj["activities"]:
            claim_of[a["id"]] = cj["claim_id"]

    # --- 1. this trade's jobs only ---
    mine = []
    for cj in fx["claim_jobs"]:
        for a in cj["activities"]:
            if a["vendor_id"] != vendor["id"]:
                continue
            mine.append({
                "job_id": a["id"], "po": a["po"], "scope": a["activity"],
                "addr": cj["addr"], "suburb": cj["suburb"],
                "start": a["start"], "finish": a["finish"],
                "pm_id": cj["pm_id"], "pm_name": cj["pm_name"], "pm_email": cj["pm_email"],
                "rc_name": cj["rc_name"], "rc_email": cj["rc_email"],
            })

    subj, html = emails.trade_invite(vendor, mine, "https://schedule.abcg.com.au/?t=DEMO")
    _write(outdir, "01_trade_invite.html", html)
    log.append(f"TRADE INVITE -> {vendor['email']}\n  {subj}\n  {len(mine)} jobs, one form\n")

    # --- 2. what the form actually receives ---
    payload = {
        "vendor_name": vendor["name"], "week_of": "2026-08-24",
        "jobs": [{k: j[k] for k in ("job_id", "po", "scope", "addr", "suburb", "start", "finish")}
                 for j in mine],
    }
    _write(outdir, "02_form_payload.json", json.dumps(payload, indent=2))

    # --- 3. trade responds, cascade runs on every move ---
    responses = fx["trade_responses"]
    impacts = {}
    for job_id, r in responses.items():
        if r["state"] != "move":
            continue
        acts = acts_of_claim[claim_of[job_id]]
        impacts[job_id] = cascade.simulate(acts, job_id, r["new_start"])

    # --- 4. split by PM ---
    buckets = {}
    by_id = {j["job_id"]: j for j in mine}
    for job_id, r in responses.items():
        j = by_id[job_id]
        b = buckets.setdefault(j["pm_email"],
                               {"name": j["pm_name"], "rc": set(), "rows": []})
        if j["rc_email"]:
            b["rc"].add(j["rc_email"])
        b["rows"].append((j, r))

    log.append(f"One submission from {vendor['name']} -> {len(buckets)} PM emails\n")
    for pm_email, b in buckets.items():
        subj, html = emails.pm_digest(b["name"], vendor["name"], b["rows"], impacts)
        slug = b["name"].split()[0].lower()
        _write(outdir, f"03_pm_{slug}.html", html)
        cc = ", ".join(sorted(b["rc"])) or "none"
        log.append(f"PM DIGEST -> {pm_email}  (cc {cc})\n  {subj}\n  {len(b['rows'])} jobs")
        for j, r in b["rows"]:
            imp = impacts.get(j["job_id"])
            note = ""
            if imp:
                hit = imp["impacted"]
                note = (f" -> breaks {len(hit)} ({', '.join(i['vendor_name'] for i in hit)})"
                        if hit else " -> absorbed downstream")
            log.append(f"    PO {j['po']:<6} {r['state']:<10}{note}")
        log.append("")

    log.append("Crunchwork writes: 0 (WRITE_BACK_ENABLED is false)")
    text = "\n".join(log)
    _write(outdir, "run_log.txt", text)
    print(text)


def _write(outdir, name, content):
    with open(os.path.join(outdir, name), "w") as f:
        f.write(content)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "out"))
