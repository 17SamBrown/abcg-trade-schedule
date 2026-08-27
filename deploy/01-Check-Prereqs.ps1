<#
    Step 1. Confirm the toolchain and AWS access before anything is created.
    Safe to re-run. Creates nothing.
#>
. "$PSScriptRoot\_config.ps1"

Write-Step "Step 1 of 9 - Prerequisites"

$tools = @(
    @{ Name = "aws";    Args = @("--version");  Hint = "https://aws.amazon.com/cli/" },
    @{ Name = "sam";    Args = @("--version");  Hint = "pip install aws-sam-cli" },
    @{ Name = "python"; Args = @("--version");  Hint = "python.org, 3.12 or later" }
)

$missing = @()
foreach ($t in $tools) {
    try {
        $v = (& $t.Name @($t.Args) 2>&1 | Select-Object -First 1)
        Write-Ok "$($t.Name)  $v"
    } catch {
        Write-Fail "$($t.Name) not found. $($t.Hint)"
        $missing += $t.Name
    }
}
if ($missing.Count) { throw "Install the missing tools above, then re-run." }

Write-Step "AWS identity"
try {
    $id = (Invoke-Aws sts get-caller-identity --output json) | ConvertFrom-Json
    Write-Ok "account $($id.Account)"
    Write-Info "arn: $($id.Arn)"
    Write-Info "region: $($Global:TSC.Region)   stage: $($Global:TSC.Stage)"
} catch {
    Write-Fail "Could not authenticate. Check the Profile in _config.ps1."
    throw
}

Write-Step "Existing stack"
try {
    $s = (Invoke-Aws cloudformation describe-stacks --stack-name $Global:TSC.StackName --output json) | ConvertFrom-Json
    Write-Warn "Stack already exists, status $($s.Stacks[0].StackStatus). Step 4 will update it."
} catch {
    Write-Info "No existing stack. Step 4 will create $($Global:TSC.StackName)."
}

Write-Host ""
Write-Ok "Prerequisites clear. Next: .\02-Set-Secret.ps1"
