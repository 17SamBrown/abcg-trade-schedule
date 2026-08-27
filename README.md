# ABCG Trade Schedule Confirmation

Weekly confirmation loop: each trade gets one link covering every job ABCG has
booked with them, confirms or moves the dates, and the response routes back to
whoever Crunchwork says owns that job.

**Nothing in this repo writes to Crunchwork.** The read replica is read-only and
all write-back is behind a disabled flag. Scheduled dates change inside
Crunchwork, actioned by a person, so the audit trail carries an actor and a
reason code when an insurer asks.

## Quick start

```powershell
cd deploy
.\01-Check-Prereqs.ps1
.\02-Check-Access.ps1
sam build
sam deploy   # see samconfig.toml, or deploy/04-Deploy-Stack.ps1
```

Dry run, which sends nothing:

```powershell
aws lambda invoke --function-name abcg-trade-dispatch-dev --region ap-southeast-2 `
  --cli-binary-format raw-in-base64-out `
  --payload '{\"mode\":\"baseline\",\"dry_run\":true,\"cap\":20}' diag.json
```

## Infrastructure

| | |
|---|---|
| Account / region | 276483283700 / ap-southeast-2 |
| VPC | vpc-0f68b12e5587df449 |
| Private subnet | subnet-02f867b1d35160325 |
| Lambda SG | sg-05cb2ec2a22ce8bab |
| Secret | `abcg/pulse2-rr` (shared with the Job Intelligence Agent) |
| NAT egress IP | **15.135.13.218 - whitelisted by Codafication. Do not rebuild the NAT or release this EIP.** |
| Form | CloudFront `d3v6eqwbgm4l2.cloudfront.net` over a private S3 bucket |

Only `DispatchFunction` and `DiscoverFunction` sit in the VPC. The API and notify
functions work entirely from the DynamoDB snapshot and never reach the database -
the same no-read boundary used in the assessment triage build.

## Crunchwork schema

Separate **databases** on one RDS instance, so Postgres cannot join across them.
Each is queried separately and joined in Python.

| Database | Used for |
|---|---|
| `purchase_orders` | `purchase_orders` + `statuses`. PO number, dates, vendor, `pulse_job_id` |
| `pulse_2` | `jobs`, `job_types`, `addresses`, `assignees` |
| `vendor_manager` | `vendors` - business_name, email, phone |
| `core` | `"Users"` (capitalised, needs quoting; camelCase columns) |

Gotchas that cost time to find:

- The site address is `jobs.address_id -> addresses.id`, **not** `addresses.job_id`.
  The PO's `to_*` fields are billing detail and are blank on many records.
- `core."Users"` has no `name` column - concatenate `firstName` and `lastName`.
  Filter `deletedAt IS NULL` and `disabled = false` or you email departed staff.
- `assignees.role` is free text and anyone can hold any role, so the role string
  is authoritative and job titles are irrelevant.
- Job status values mix en dashes and hyphens
  ("Repairs Stalled – Maintenance" vs "Repairs Stalled - Restoration"), so
  status matching is normalised. Never compare these exactly.

## What gets asked, and what does not

Open POs are `status = 'Sent'` only. Reconciled, Draft, Cancelled and Pending
Approval are not live work.

Job types are an **allow list** - `Works` and `IAG Completion`. An unrecognised
type is excluded, so a new type in Crunchwork cannot silently start receiving
confirmations. Rectification is remedial and scheduled differently; make-safe is
done inside 48 hours so a weekly form always arrives too late.

Excluded job statuses: finished or settled work, unapproved work, work waiting
on someone outside ABCG, and stalled work. See `EXCLUDED_JOB_STATUSES`.

All three filters **fail closed**. If job status or type cannot be read the whole
dispatch aborts rather than sending, because confirming dates on cancelled or
cash-settled work sends a trade to a site with no work.

## Horizon

28 day window, weekly send. A trade would otherwise see the same job four times,
so the window is wide and the ask is narrow - only what is new, changed, or has
gone stale. See `horizon.py`.

Bands: overdue, commit (0-7 days), plan (8-14), outlook (15-28). An answer given
in the outlook band does not survive into the commit band; a commitment made four
weeks out is not the same as one made four days out.

A trade with nothing to answer gets no email at all.

## Responses

| Trade taps | Meaning | Date effect |
|---|---|---|
| Dates confirmed | The booked window is accepted | None |
| On site now | Work has started | None |
| Looks right | No known problem, outlook band only | None |
| Work completed | **A claim, not a verified completion** | None |
| Need to move | Request with a reason and proposed date | None until a PM accepts |
| Cannot do this job | Handback - reallocation needed | None |

## Routing

`pulse_2.assignees` gives job -> user + role. Project Manager is primary,
Supervisor and Repair Coordinator are CC'd. Two PMs on a job means two emails -
a duplicate beats the right person not being told. No PM falls back through
Supervisor, Repair Coordinator, Manager, and the digest says which.

## Outstanding

- SES domain verification for `abcg.com.au` (DKIM CNAMEs + SPF `include:amazonses.com`)
- Suppression list for test and internal vendors (`Soumya Test`,
  `TEST TRADE Perry the painter`, `One Off Material Supplier - Robertson`)
- Decide on `Awaiting Insurer` (37 jobs) - excluded or not
- `Accept proposed date` workflow in Crunchwork before write-back is enabled
- Custom domain for the form
