<#
    Step 5. Point the form at the deployed API and upload it to S3.
    Safe to re-run - overwrites the object.
#>
. "$PSScriptRoot\_config.ps1"

Write-Step "Step 5 of 9 - Publish the form"

$stackFile = Join-Path $Global:TSC.OutDir "stack.json"
if (-not (Test-Path $stackFile)) { throw "Run .\04-Deploy-Stack.ps1 first." }
$stack = Get-Content $stackFile -Raw | ConvertFrom-Json

$src = Join-Path $Global:TSC.Root "web\index.html"
if (-not (Test-Path $src)) { throw "Expected the form at $src" }

$staged = Join-Path $Global:TSC.OutDir "index.html"
$html = Get-Content $src -Raw
if ($html -notmatch 'const API_BASE') {
    Write-Warn "The form has no API_BASE constant. It will run on mock data only."
} else {
    $html = $html -replace 'const API_BASE\s*=\s*"[^"]*";', "const API_BASE = `"$($stack.ApiEndpoint)`";"
    Write-Ok "API_BASE set to $($stack.ApiEndpoint)"
}
Set-Content -Path $staged -Value $html -Encoding UTF8

Invoke-Aws s3 cp $staged "s3://$($stack.FormBucket)/index.html" `
    --content-type "text/html; charset=utf-8" `
    --cache-control "no-cache" | Out-Null
Write-Ok "uploaded to s3://$($stack.FormBucket)/index.html"

Write-Warn "The bucket is private by design."
Write-Info "Put CloudFront in front with origin access control, then point"
Write-Info "$($Global:TSC.FormBaseUrl) at the distribution."
Write-Info "Until that exists, test the form locally by opening $staged."

Write-Host ""
Write-Ok "Next: .\06-Baseline-DryRun.ps1"
