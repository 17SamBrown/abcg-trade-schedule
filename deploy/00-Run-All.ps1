<#
    Guided walkthrough. Runs steps 1 to 6 with a pause between each.
    Stops before anything sends. Steps 7 to 9 are deliberately manual.
#>
. "$PSScriptRoot\_config.ps1"

$steps = @(
    @{ N = "01-Check-Prereqs.ps1";   D = "Toolchain and AWS access" }
    @{ N = "02-Set-Secret.ps1";      D = "pulse_2 credentials" }
    @{ N = "03-Verify-Ses.ps1";      D = "SES identities" }
    @{ N = "04-Deploy-Stack.ps1";    D = "Deploy the stack" }
    @{ N = "05-Publish-Form.ps1";    D = "Publish the form" }
    @{ N = "06-Baseline-DryRun.ps1"; D = "Baseline dry run, sends nothing" }
)

Write-Host ""
Write-Host "  ABCG Trade Schedule Confirmation - guided build" -ForegroundColor Cyan
Write-Host "  Stage $($Global:TSC.Stage), region $($Global:TSC.Region)" -ForegroundColor Gray
Write-Host ""
foreach ($s in $steps) { Write-Host "    $($s.N)  -  $($s.D)" -ForegroundColor Gray }
Write-Host ""
Write-Host "  Steps 7 to 9 send email and are run by hand." -ForegroundColor Yellow
Write-Host ""

foreach ($s in $steps) {
    $go = Read-Host "Run $($s.N)? [Y/n/q]"
    if ($go -eq "q") { Write-Info "Stopped."; return }
    if ($go -eq "n") { Write-Info "Skipped $($s.N)"; continue }
    & (Join-Path $PSScriptRoot $s.N)
}

Write-Host ""
Write-Ok "Build complete to the dry run. Review out\baseline-dryrun.json before sending."
