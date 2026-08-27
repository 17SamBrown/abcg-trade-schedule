<#
    Step 2. Confirm the existing pulse_2 secret and network are reachable.
    Creates nothing. The secret already exists from the Job Intelligence
    Agent build - we reuse it rather than making a second copy.
#>
. "$PSScriptRoot\_config.ps1"

Write-Step "Step 2 of 8 - Existing pulse_2 access"

try {
    $sec = (Invoke-Aws secretsmanager describe-secret --secret-id $Global:TSC.SecretArn --output json) | ConvertFrom-Json
    Write-Ok "secret found: $($sec.Name)"
} catch {
    Write-Fail "Cannot read $($Global:TSC.SecretArn)"
    throw
}

Write-Step "Network"
$sn = (Invoke-Aws ec2 describe-subnets --subnet-ids $Global:TSC.PrivateSubnet --output json) | ConvertFrom-Json
Write-Ok "private subnet: $($Global:TSC.PrivateSubnet) in $($sn.Subnets[0].AvailabilityZone)"

$sg = (Invoke-Aws ec2 describe-security-groups --group-ids $Global:TSC.LambdaSg --output json) | ConvertFrom-Json
Write-Ok "security group: $($sg.SecurityGroups[0].GroupName)"

$nat = (Invoke-Aws ec2 describe-nat-gateways --filter "Name=subnet-id,Values=$($Global:TSC.PrivateSubnet)" --output json) | ConvertFrom-Json
$rt = (Invoke-Aws ec2 describe-route-tables --filters "Name=association.subnet-id,Values=$($Global:TSC.PrivateSubnet)" --output json) | ConvertFrom-Json
$natRoute = $rt.RouteTables[0].Routes | Where-Object { $_.NatGatewayId }
if ($natRoute) {
    Write-Ok "0.0.0.0/0 routes via $($natRoute.NatGatewayId)"
    $ngw = (Invoke-Aws ec2 describe-nat-gateways --nat-gateway-ids $natRoute.NatGatewayId --output json) | ConvertFrom-Json
    $ip = $ngw.NatGateways[0].NatGatewayAddresses[0].PublicIp
    Write-Ok "outbound IP: $ip"
    if ($ip -ne "15.135.13.218") {
        Write-Fail "Expected 15.135.13.218 - the address Codafication whitelisted."
        Write-Warn "If the NAT was rebuilt, the whitelist is broken and reads will fail."
    }
} else {
    Write-Fail "No NAT route on the private subnet. Lambda will not reach the replica."
}

Write-Host ""
Write-Ok "Next: .\03-Verify-Ses.ps1"
