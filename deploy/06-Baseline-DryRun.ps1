<#
    Step 6. The baseline dry run. THIS SENDS NOTHING.

    Reads every open PO from Crunchwork, groups by vendor, and reports what the
    first send would look like: how many trades, how many POs each, how many have
    no dates at all. This is your first honest picture of the exposure.

    Re-runnable as often as you like. It is read-only.
#>
param(
    [int]$Cap = 20   # max POs per vendor in the first send. 0 = no cap.
)
. "$PSScriptRoot\_config.ps1"

Write-Step "Step 6 of 9 - Baseline dry run (nothing is sent)"

$stack = Get-Content (Join-Path $Global:TSC.OutDir "stack.json") -Raw | ConvertFrom-Json

$payload = @{ mode = "baseline"; dry_run = $true; cap = $Cap } | ConvertTo-Json -Compress
$tmpIn  = New-TemporaryFile
$tmpOut = Join-Path $Global:TSC.OutDir "baseline-dryrun.json"
Set-Content -Path $tmpIn -Value $payload -Encoding UTF8 -NoNewline

Write-Info "invoking $($stack.DispatchFunction)"
Invoke-Aws lambda invoke `
    --function-name $stack.DispatchFunction `
    --cli-binary-format raw-in-base64-out `
    --payload "file://$tmpIn" `
    --log-type Tail `
    --query "LogResult" --output text $tmpOut | Set-Variable logB64
Remove-Item $tmpIn -Force -ErrorAction SilentlyContinue

$result = Get-Content $tmpOut -Raw | ConvertFrom-Json
if ($result.errorMessage) {
    Write-Fail $result.errorMessage
    Write-Info "Most likely the pulse_2 query in src/common/pulse.py does not match your schema."
    if ($logB64) {
        $log = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($logB64))
        Save-Log "baseline-dryrun-error" $log
    }
    throw "Dry run failed."
}

Write-Step "What the first send would look like"
$rows = $result.sent | Sort-Object -Property @{ Expression = { $_.ask } } -Descending
$rows | Format-Table -AutoSize @(
    @{ L = "Vendor";   E = { $_.vendor } }
    @{ L = "Asking";   E = { $_.ask } }
    @{ L = "Undated";  E = { $_.undated } }
    @{ L = "Deferred"; E = { $_.deferred } }
) | Out-String | Write-Host

$totalVendors  = @($result.sent).Count
$totalAsk      = ($result.sent | Measure-Object -Property ask -Sum).Sum
$totalUndated  = ($result.sent | Measure-Object -Property undated -Sum).Sum
$totalDeferred = ($result.sent | Measure-Object -Property deferred -Sum).Sum

Write-Ok   "$totalVendors vendors would receive a form"
Write-Ok   "$totalAsk POs asked about in total"
if ($totalUndated) { Write-Warn "$totalUndated POs have NO dates on file - the trade will be asked to propose them" }
if ($totalDeferred) { Write-Warn "$totalDeferred POs deferred by the cap of $Cap - they go in the second baseline send" }

if ($result.skipped) {
    Write-Step "Skipped"
    $result.skipped | Format-Table -AutoSize | Out-String | Write-Host
    Write-Warn "Vendors with no email on the record cannot be contacted. Fix these in the Vendor Register."
}

$biggest = $rows | Select-Object -First 1
if ($biggest -and $biggest.ask -gt 25) {
    Write-Warn "$($biggest.vendor) would be asked about $($biggest.ask) POs in one form."
    Write-Info "Consider re-running with a lower -Cap. Partial submit is enabled on baseline,"
    Write-Info "so they can answer over two sittings, but a very long list still deters people."
}

Save-Log "baseline-dryrun" ($result | ConvertTo-Json -Depth 6)
Write-Host ""
Write-Ok "Review the table above. When you are happy: .\07-Baseline-Send.ps1"
