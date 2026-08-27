<#
    The call list. Read only.

    Ranks non-responders by exposure rather than alphabetically, so whoever picks
    up the phone starts with the trade whose silence costs the most.

    Four states, four different conversations:
      SUBMITTED   done, no action
      PARTIAL     started and stopped. Their link still works. "Need a hand?"
      OPENED      read it, answered nothing. Something put them off. Ask what
      UNOPENED    never loaded the form. Wrong address, spam filter, or ignored

    Usage:
      .\10-Follow-Up-List.ps1
      .\10-Follow-Up-List.ps1 -Export
#>
param([switch]$Export, [int]$MinPos = 1)
. "$PSScriptRoot\_config.ps1"

Write-Step "Trade follow-up list"

$table = "abcg-trade-dispatch-$($Global:TSC.Stage)"
$items = ((Invoke-Aws dynamodb scan --table-name $table --output json) | ConvertFrom-Json).Items
if (-not $items) { Write-Warn "No dispatches found."; return }

$now = Get-Date
$rows = foreach ($i in $items) {
    $jobs      = @($i.jobs.L)
    $asked     = @($jobs | Where-Object { -not $_.M.carried.BOOL })
    $responses = if ($i.responses.M) { @($i.responses.M.PSObject.Properties) } else { @() }
    if ($asked.Count -lt $MinPos) { continue }

    $sent   = if ($i.sent_at.S) { [datetime]$i.sent_at.S } else { $null }
    $opened = if ($i.opened_at.S) { [datetime]$i.opened_at.S } else { $null }
    $days   = if ($sent) { [math]::Round(($now - $sent).TotalDays, 1) } else { 0 }

    $state = if ($i.submitted_at.S)      { "SUBMITTED" }
             elseif ($responses.Count)   { "PARTIAL"   }
             elseif ($opened)            { "OPENED"    }
             else                        { "UNOPENED"  }

    # soonest booked start still unanswered - the thing that actually bites
    $answered = $responses | ForEach-Object { $_.Name }
    $pending  = $asked | Where-Object { $_.M.job_id.S -notin $answered }
    $soonest  = ($pending | ForEach-Object { $_.M.start.S } |
                 Where-Object { $_ } | Sort-Object | Select-Object -First 1)
    $daysToStart = if ($soonest) {
        [math]::Round(([datetime]$soonest - $now).TotalDays, 0)
    } else { 999 }

    # exposure: unanswered POs weighted by how soon the first one starts
    $urgency = if ($daysToStart -le 0) { 4 }
               elseif ($daysToStart -le 7)  { 3 }
               elseif ($daysToStart -le 14) { 2 }
               else { 1 }

    [pscustomobject]@{
        Vendor      = $i.vendor_name.S
        VendorId    = $i.vendor_id.S
        State       = $state
        Asked       = $asked.Count
        Answered    = $responses.Count
        Pending     = $pending.Count
        NextStart   = $soonest
        DaysToStart = $daysToStart
        DaysSince   = $days
        Opens       = if ($i.open_count.N) { [int]$i.open_count.N } else { 0 }
        Exposure    = $pending.Count * $urgency
    }
}

$open = $rows | Where-Object { $_.State -ne "SUBMITTED" } | Sort-Object Exposure -Descending

Write-Step "Summary"
$rows | Group-Object State | Sort-Object Count -Descending |
    Format-Table -AutoSize @{L="State";E={$_.Name}}, @{L="Vendors";E={$_.Count}},
                           @{L="POs pending";E={($_.Group | Measure-Object Pending -Sum).Sum}} |
    Out-String | Write-Host

if (-not $open) { Write-Ok "Everyone has responded. Nothing to chase."; return }

Write-Step "Call list, most exposed first"
$open | Select-Object -First 25 Vendor, State, Pending, DaysToStart, DaysSince, Opens |
    Format-Table -AutoSize | Out-String | Write-Host

Write-Step "What to say"
Write-Info "UNOPENED  - never loaded the form. Check the email address on the vendor"
Write-Info "            record before assuming they ignored it."
Write-Info "OPENED    - they read it and answered nothing. Worth asking what stopped"
Write-Info "            them. Usually the list was too long or they were on site."
Write-Info "PARTIAL   - they started. Their link still works, so a nudge is enough."

$urgent = $open | Where-Object { $_.DaysToStart -le 2 -and $_.DaysToStart -ne 999 }
if ($urgent) {
    Write-Step "Ring these today"
    Write-Warn "$($urgent.Count) vendor(s) have unanswered work starting within 48 hours"
    $urgent | Select-Object Vendor, Pending, NextStart, State |
        Format-Table -AutoSize | Out-String | Write-Host
}

if ($Export) {
    $path = Join-Path $Global:TSC.OutDir ("follow-up-{0}.csv" -f (Get-Date -Format "yyyyMMdd"))
    $open | Export-Csv -Path $path -NoTypeInformation
    Write-Ok "exported: $path"
}
