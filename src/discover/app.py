"""
One-shot schema discovery. Read only, prints what it finds.
"""
import json

from common import db


def handler(event, context):
    out = {}

    try:
        out["po_statuses"] = db.query("""
            SELECT s.name, COUNT(po.id) AS open_pos
            FROM public.statuses s
            LEFT JOIN public.purchase_orders po
                   ON po.status_id = s.id AND po.deleted_at IS NULL
            GROUP BY s.name ORDER BY 2 DESC
        """, database="purchase_orders")
    except Exception as e:  # noqa: BLE001
        out["po_statuses"] = f"FAILED: {e}"

    for label, dbname, table in [
        ("purchase_orders", "purchase_orders", "purchase_orders"),
        ("vendors", "vendor_manager", "vendors"),
        ("jobs", "pulse_2", "jobs"),
        ("assignments", "pulse_2", "assignments"),
        ("users", "pulse_2", "users"),
    ]:
        try:
            cols = db.describe(dbname, table)
            out[f"cols_{label}"] = [c["column_name"] for c in cols]
        except Exception as e:  # noqa: BLE001
            out[f"cols_{label}"] = f"FAILED: {e}"

    # the job statuses that decide whether a confirmation may be sent at all
    try:
        out["job_statuses"] = db.query("""
            SELECT s.name, COUNT(j.id) AS jobs
            FROM public.statuses s
            LEFT JOIN public.jobs j ON j.status_id = s.id
            GROUP BY s.name ORDER BY 2 DESC
        """, database="pulse_2")
    except Exception as e:  # noqa: BLE001
        out["job_statuses"] = f"FAILED: {e}"

    try:
        out["vendor_contactability"] = db.query("""
            SELECT COUNT(*) AS total,
                   COUNT(email) AS with_email,
                   COUNT(*) FILTER (WHERE active) AS active
            FROM public.vendors WHERE deleted_at IS NULL
        """, database="vendor_manager")
    except Exception as e:  # noqa: BLE001
        out["vendor_contactability"] = f"FAILED: {e}"

    print(json.dumps(out, indent=2, default=str))
    return out
