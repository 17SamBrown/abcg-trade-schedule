# Deploy - PowerShell

Run these in order from this folder. Every script is safe to re-run.

```powershell
cd deploy
.\00-Run-All.ps1        # guided, walks steps 1 to 6 with a pause between each
```

Or one at a time:

| Script | Does | Sends email? |
|---|---|---|
| `01-Check-Prereqs.ps1` | aws cli, sam, python, AWS identity | no |
| `02-Set-Secret.ps1` | pulse_2 endpoint and key into Secrets Manager | no |
| `03-Verify-Ses.ps1` | Verify sender and test recipient, report sandbox status | AWS sends verification links |
| `04-Deploy-Stack.ps1` | `sam build` and `sam deploy` | no |
| `05-Publish-Form.ps1` | Point the form at the API, upload to S3 | no |
| `06-Baseline-DryRun.ps1` | **Every open PO, grouped by vendor. Reports only.** | no |
| `07-Baseline-Send.ps1` | The first real send | **yes** |
| `08-Check-Responses.ps1` | Who answered, what they said, who went quiet | no |
| `09-Enable-Weekly.ps1` | Switch to the rolling 28 day schedule | no |

**Edit `_config.ps1` first.** Stage, region, AWS profile, sender address.

## Defaults that matter

The stack deploys with `TestMode=true` (all mail to you, real recipient in the
subject), `WRITE_BACK_ENABLED=false` (nothing touches Crunchwork) and the weekly
EventBridge rule **disabled**. Nothing sends until you run step 7 by hand.

## The baseline run

Step 6 is the important one and it sends nothing. It reads every open PO and
tells you what the first send would look like per vendor, including how many POs
have no dates at all. Read that table before step 7.

```powershell
.\06-Baseline-DryRun.ps1 -Cap 20     # cap the first send at 20 POs per vendor
.\07-Baseline-Send.ps1 -VendorIds "v-123","v-456"   # pilot, three friendly trades
.\08-Check-Responses.ps1 -Detail     # includes the delay reason breakdown
```

Baseline allows partial submission. A trade with forty POs can answer half, close
the link, and come back. The token lasts 21 days.

## Going live

```powershell
.\07-Baseline-Send.ps1 -Live         # requires typing SEND
.\09-Enable-Weekly.ps1               # requires typing ENABLE
.\09-Enable-Weekly.ps1 -Disable      # stop the weekly send at any time
```
