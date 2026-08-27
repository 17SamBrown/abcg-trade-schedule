<#
    Step 4. sam build and sam deploy.
    Deploys with TestMode on and write-back off. Both are deliberate.
    Safe to re-run - CloudFormation updates in place.
#>
. "$PSScriptRoot\_config.ps1"

Write-Step "Step 4 of 9 - Deploy"

$secretArn = $Global:TSC.SecretArn
Write-Info "Reusing the existing pulse_2 secret: $secretArn"

Push-Location $Global:TSC.Root
try {
    Write-Info "sam build"
    & sam build 2>&1 | Tee-Object -Variable buildOut | Out-Null
    if ($LASTEXITCODE -ne 0) { $buildOut | Write-Host; throw "sam build failed." }
    Write-Ok "build complete"

    $params = @(
        "Stage=$($Global:TSC.Stage)"
        "FormBaseUrl=$($Global:TSC.FormBaseUrl)"
        "SenderEmail=$($Global:TSC.SenderEmail)"
        "TestModeRecipient=$($Global:TSC.TestRecipient)"
        "TestMode=true"
        "PulseSecretArn=$secretArn"
        "PrivateSubnetId=$($Global:TSC.PrivateSubnet)"
        "LambdaSecurityGroupId=$($Global:TSC.LambdaSg)"
    )

    $args = @(
        "deploy"
        "--stack-name", $Global:TSC.StackName
        "--region", $Global:TSC.Region
        "--capabilities", "CAPABILITY_IAM"
        "--no-confirm-changeset"
        "--no-fail-on-empty-changeset"
        "--resolve-s3"
        "--parameter-overrides"
    ) + $params
    if ($Global:TSC.Profile -and $Global:TSC.Profile -ne "default") {
        $args += @("--profile", $Global:TSC.Profile)
    }

    Write-Info "sam deploy"
    & sam @args 2>&1 | Tee-Object -Variable deployOut | Out-Null
    if ($LASTEXITCODE -ne 0) { $deployOut | Write-Host; throw "sam deploy failed." }
    Save-Log "deploy" $deployOut
    Write-Ok "stack deployed"
} finally { Pop-Location }

Write-Step "Outputs"
$api    = Get-StackOutput "ApiEndpoint"
$bucket = Get-StackOutput "FormBucket"
$fn     = Get-StackOutput "DispatchFunctionName"

Write-Ok  "api:      $api"
Write-Ok  "bucket:   $bucket"
Write-Ok  "dispatch: $fn"

@{ ApiEndpoint = $api; FormBucket = $bucket; DispatchFunction = $fn } |
    ConvertTo-Json | Set-Content (Join-Path $Global:TSC.OutDir "stack.json")

Write-Warn "TestMode is ON. All mail goes to $($Global:TSC.TestRecipient)."
Write-Warn "Write-back is OFF. Nothing writes to Crunchwork."
Write-Warn "The weekly schedule is DISABLED. Sends only happen when you run them."

Write-Host ""
Write-Ok "Next: .\05-Publish-Form.ps1"
