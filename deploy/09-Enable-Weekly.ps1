<#
    Step 9. Switch from baseline to the rolling 4 week schedule.

    Do NOT run this until baseline has settled. Once the weekly rule is on, every
    trade gets a form each Monday covering the next 28 days, and only for jobs
    that are new, changed or stale.
#>
param([switch]$Disable)
. "$PSScriptRoot\_config.ps1"

Write-Step "Step 9 of 9 - Weekly schedule"

$ruleName = "abcg-trade-weekly-$($Global:TSC.Stage)"

if ($Disable) {
    Invoke-Aws events disable-rule --name $ruleName | Out-Null
    Write-Ok "Weekly rule DISABLED. No automatic sends."
    return
}

Write-Warn "Before enabling, confirm all of the following:"
$checks = @(
    "Baseline responses reviewed and the PM digests look right"
    "At least one PM has confirmed the digest is useful"
    "Vendors with no email have been fixed in the Vendor Register"
    "SES is out of the sandbox, or you accept test-mode routing"
)
foreach ($c in $checks) { Write-Info "  [ ] $c" }
Write-Host ""
$confirm = Read-Host "Type ENABLE to turn on the Monday 6am AEST send"
if ($confirm -ne "ENABLE") { Write-Info "Cancelled."; return }

Invoke-Aws events enable-rule --name $ruleName | Out-Null
Write-Ok "Weekly rule ENABLED"
Write-Info "Fires 20:00 UTC Sunday = 06:00 Monday AEST"
Write-Info "Horizon: 28 days rolling. Carry-forward is on, so quiet trades get no email."
Write-Info "Disable at any time with: .\09-Enable-Weekly.ps1 -Disable"

Write-Step "Still outstanding"
Write-Info "Write-back to Crunchwork is OFF."
Write-Info "Turn it on only once the Accept proposed date workflow exists,"
Write-Info "by setting WRITE_BACK_ENABLED=true on the notify function."
