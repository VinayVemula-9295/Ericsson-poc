<#
Run locally to configure AWS SSO administrator profiles. No credentials are printed
or copied to the application server. Browser authentication is performed by you.
#>
param()
$ErrorActionPreference = 'Stop'
$awsCommand = Get-Command aws -ErrorAction Stop
$accounts = @(
    @{ Profile = 'iipp-management-admin'; Account = '704298683641' },
    @{ Profile = 'iipp-demo-admin'; Account = '333347372766' },
    @{ Profile = 'iipp-test-admin'; Account = '695729002971' }
)
Write-Host 'Use SSO session name iipp-admin for all three profiles.'
Write-Host 'Use your portal SSO Start URL and SSO Region; choose the existing administrator permission set.'
Write-Host 'Set CLI default region ap-south-1 and output json. Do not share credentials in chat.'
foreach ($entry in $accounts) {
    Write-Host "Configure $($entry.Profile) for account $($entry.Account)"
    & $awsCommand.Source configure sso --profile $entry.Profile
    if ($LASTEXITCODE -ne 0) { throw "SSO configuration failed for $($entry.Profile)" }
    $identityText = & $awsCommand.Source sts get-caller-identity --profile $entry.Profile --region ap-south-1 --output json
    if ($LASTEXITCODE -ne 0) { throw "Sign-in verification failed for $($entry.Profile)" }
    $identity = $identityText | ConvertFrom-Json
    if ($identity.Account -ne $entry.Account -or $identity.Arn.EndsWith(':root')) {
        throw "Wrong account or root identity selected for $($entry.Profile); expected non-root account $($entry.Account)"
    }
    Write-Host "Verified $($entry.Profile): account $($entry.Account)"
}
Write-Host 'All three account identities verified. Setup can now check IAM, keys and networking.'
