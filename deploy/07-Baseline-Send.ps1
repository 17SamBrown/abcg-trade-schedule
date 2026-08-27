<#
    Step 7. The baseline send. THIS SENDS EMAIL.

    While TestMode is on, every message goes to the test recipient with the real
    recipient in the subject line. Run it that way first, read what arrives, then
    set -Live to reach actual trades.

    -VendorIds limits the send. Use it. Do not send to everyone on the first run.
#>
param(
    [string[]]$VendorIds = @(),
    [int]$Cap = 20,
    [switch]$Live
)
. "$PSScriptRoot\_config.ps1"

Write-Step "Step 7 of 9 - Baseline send"

$stack = Get-Content (Join-Path $Global:TSC.OutDir "stack.json") -Raw | ConvertFrom-Json

if ($Live) {
    Write-Warn "LIVE MODE. Set TestMode=false on the stack for this to reach real trades."
    Write-Warn "This sends email to actual vendors and cannot be recalled."
    $confirm = Read-Host "Type SEND to continue"
    if ($confirm -ne "SEND") { Write-Info "Cancelled."; return }
} else {
    Write-Info "TestMode routing. Mail goes to $($Global:TSC.TestRecipient)."
}

if (-not $VendorIds.Count) {
    Write-Warn "No -VendorIds given. This will send to EVERY vendor with open POs."
    $confirm = Read-Host "Type ALL to continue, or Ctrl+C to pick specific vendors"
    if ($confirm -ne "ALL") { Write-Info "Cancelled."; return }
}

$body = @{ mode = "baseline"; dry_run = $false; cap = $Cap }
if ($VendorIds.Count) { $body.vendor_ids = $VendorIds }

$tmpIn  = New-TemporaryFile
$tmpOut = Join-Path $Global:TSC.OutDir "baseline-send.json"
Set-Content -Path $tmpIn -Value ($body | ConvertTo-Json -Compress) -Encoding UTF8 -NoNewline

Invoke-Aws lambda invoke `
    --function-name $stack.DispatchFunction `
    --cli-binary-format raw-in-base64-out `
    --payload "file://$tmpIn" $tmpOut | Out-Null
Remove-Item $tmpIn -Force -ErrorAction SilentlyContinue

$result = Get-Content $tmpOut -Raw | ConvertFrom-Json
if ($result.errorMessage) { Write-Fail $result.errorMessage; throw "Send failed." }

Write-Step "Sent"
$result.sent | Format-Table -AutoSize @(
    @{ L = "Vendor";  E = { $_.vendor } }
    @{ L = "Asking";  E = { $_.ask } }
    @{ L = "Undated"; E = { $_.undated } }
) | Out-String | Write-Host

Write-Ok "$(@($result.sent).Count) forms issued"
if ($result.skipped) { Write-Warn "$(@($result.skipped).Count) vendors skipped - see the log" }

Save-Log "baseline-send" ($result | ConvertTo-Json -Depth 6)
Write-Host ""
Write-Ok "Track responses with: .\08-Check-Responses.ps1"
