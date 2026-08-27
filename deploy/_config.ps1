<#
    ABCG Trade Schedule Confirmation - shared configuration.
    Edit this once. Every numbered script dot-sources it.
#>

$ErrorActionPreference = "Stop"

# ---- edit these ----
$Global:TSC = @{
    Stage           = "dev"
    Region          = "ap-southeast-2"
    Profile         = "default"                    # aws cli named profile
    StackName       = "abcg-trade-schedule-dev"
    SenderEmail     = "trades@abcg.com.au"
    TestRecipient   = "sam@abcg.com.au"
    FormBaseUrl     = "https://schedule.abcg.com.au"
    SecretArn       = "arn:aws:secretsmanager:ap-southeast-2:276483283700:secret:abcg/pulse2-rr-FHXj6k"
    PrivateSubnet   = "subnet-02f867b1d35160325"
    LambdaSg        = "sg-05cb2ec2a22ce8bab"
    ArtifactBucket  = ""                           # leave blank to let sam create one
}
# --------------------

$Global:TSC.Root      = Split-Path $PSScriptRoot -Parent
$Global:TSC.LogDir    = Join-Path $Global:TSC.Root "logs"
$Global:TSC.OutDir    = Join-Path $Global:TSC.Root "out"

New-Item -ItemType Directory -Force -Path $Global:TSC.LogDir, $Global:TSC.OutDir | Out-Null

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host "  $Text" -ForegroundColor Cyan
    Write-Host ("  " + ("-" * $Text.Length)) -ForegroundColor DarkCyan
}

function Write-Ok    { param([string]$T) Write-Host "  [ok]   $T" -ForegroundColor Green }
function Write-Warn  { param([string]$T) Write-Host "  [warn] $T" -ForegroundColor Yellow }
function Write-Fail  { param([string]$T) Write-Host "  [fail] $T" -ForegroundColor Red }
function Write-Info  { param([string]$T) Write-Host "         $T" -ForegroundColor Gray }

function Invoke-Aws {
    <# Thin wrapper so every call carries region and profile consistently. #>
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    $full = @($Args) + @("--region", $Global:TSC.Region)
    if ($Global:TSC.Profile -and $Global:TSC.Profile -ne "default") {
        $full += @("--profile", $Global:TSC.Profile)
    }
    $out = & aws @full 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($out | Out-String) }
    return $out
}

function Get-StackOutput {
    param([string]$Key)
    $json = Invoke-Aws cloudformation describe-stacks `
        --stack-name $Global:TSC.StackName --output json
    $stack = ($json | ConvertFrom-Json).Stacks[0]
    ($stack.Outputs | Where-Object { $_.OutputKey -eq $Key }).OutputValue
}

function Save-Log {
    param([string]$Name, [object]$Content)
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $path  = Join-Path $Global:TSC.LogDir "$stamp-$Name.txt"
    $Content | Out-String | Set-Content -Path $path -Encoding UTF8
    Write-Info "log: $path"
    return $path
}
