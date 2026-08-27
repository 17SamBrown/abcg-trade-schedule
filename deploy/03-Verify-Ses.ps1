<#
    Step 3. Verify the sender and, while sandboxed, every test recipient.
    SES will email each address a confirmation link that must be clicked.
    Safe to re-run.
#>
. "$PSScriptRoot\_config.ps1"

Write-Step "Step 3 of 9 - SES identities"

$addresses = @($Global:TSC.SenderEmail, $Global:TSC.TestRecipient) | Select-Object -Unique

foreach ($a in $addresses) {
    Invoke-Aws ses verify-email-identity --email-address $a | Out-Null
    Write-Ok "verification requested: $a"
}
Write-Info "Each address gets an email from AWS. Click the link before sending anything."

Write-Step "Sandbox status"
$q = (Invoke-Aws ses get-send-quota --output json) | ConvertFrom-Json
Write-Info ("24 hour send quota: {0}" -f $q.Max24HourSend)
if ($q.Max24HourSend -le 200) {
    Write-Warn "This account is in the SES sandbox."
    Write-Info "Mail will only reach verified addresses, which is fine for the pilot."
    Write-Info "Request production access before sending to real trades:"
    Write-Info "  https://console.aws.amazon.com/ses/home#/account"
} else {
    Write-Ok "Out of the sandbox. Mail can reach any address."
}

Write-Step "Verified so far"
$v = (Invoke-Aws ses list-identities --identity-type EmailAddress --output json) | ConvertFrom-Json
$status = (Invoke-Aws ses get-identity-verification-attributes --identities $v.Identities --output json) | ConvertFrom-Json
foreach ($k in $status.VerificationAttributes.PSObject.Properties) {
    $s = $k.Value.VerificationStatus
    if ($s -eq "Success") { Write-Ok "$($k.Name)  $s" } else { Write-Warn "$($k.Name)  $s" }
}

Write-Host ""
Write-Ok "Next: .\04-Deploy-Stack.ps1"
