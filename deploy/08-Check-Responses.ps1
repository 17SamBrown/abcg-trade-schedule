<#
    Step 8. Who has responded, who has not, and what they said.
    Read-only. Run it as often as you like during the baseline window.
#>
param([switch]$Detail)
. "$PSScriptRoot\_config.ps1"

Write-Step "Step 8 of 9 - Response tracking"

$table = "abcg-trade-dispatch-$($Global:TSC.Stage)"
$json  = Invoke-Aws dynamodb scan --table-name $table --output json
$items = ($json | ConvertFrom-Json).Items

if (-not $items) { Write-Warn "No dispatches found. Has step 7 run?"; return }

$rows = foreach ($i in $items) {
    $responses = if ($i.responses.M) { $i.responses.M.PSObject.Properties } else { @() }
    $jobCount  = @($i.jobs.L).Count
    $answered  = @($responses).Count
    $states    = @{}
    foreach ($r in $responses) { 
        $s = $r.Value.M.state.S
        $states[$s] = 1 + ($states[$s] ?? 0)
    }
    [pscustomobject]@{
        Vendor    = $i.vendor_name.S
        Jobs      = $jobCount
        Answered  = $answered
        Status    = if ($i.submitted_at.S) { "submitted" }
                    elseif ($answered) { "partial" } else { "no response" }
        Confirmed = $states["confirmed"] ?? 0
        Proposed  = $states["propose"]   ?? 0
        Moves     = $states["move"]      ?? 0
        Complete  = $states["completed"] ?? 0
        Sent      = $i.sent_at.S
    }
}

$rows | Sort-Object Status, Vendor | Format-Table -AutoSize | Out-String | Write-Host

$total   = @($rows).Count
$done    = @($rows | Where-Object Status -eq "submitted").Count
$partial = @($rows | Where-Object Status -eq "partial").Count
$none    = @($rows | Where-Object Status -eq "no response").Count

Write-Ok   "$done of $total submitted"
if ($partial) { Write-Info "$partial partially answered - the link still works for them" }
if ($none) {
    Write-Warn "$none have not responded at all"
    Write-Info "Non-response is a signal in itself. Feed these to Trade Procurement."
    ($rows | Where-Object Status -eq "no response" | Select-Object -Expand Vendor) |
        ForEach-Object { Write-Info "  - $_" }
}

if ($Detail) {
    Write-Step "Delay reasons"
    $reasons = @{}
    foreach ($i in $items) {
        if (-not $i.responses.M) { continue }
        foreach ($r in $i.responses.M.PSObject.Properties) {
            $rn = $r.Value.M.reason.S
            if ($rn) { $reasons[$rn] = 1 + ($reasons[$rn] ?? 0) }
        }
    }
    if ($reasons.Count) {
        $reasons.GetEnumerator() | Sort-Object Value -Descending |
            Format-Table -AutoSize @{L="Reason";E={$_.Key}}, @{L="Count";E={$_.Value}} |
            Out-String | Write-Host
        if ($reasons["Waiting on ABCG"]) {
            Write-Warn "'Waiting on ABCG' appeared $($reasons['Waiting on ABCG']) times. That one is about us."
        }
    } else { Write-Info "No date changes requested yet." }
}

Save-Log "responses" ($rows | ConvertTo-Json -Depth 4)
Write-Host ""
Write-Ok "When baseline is done: .\09-Enable-Weekly.ps1"
