# Shop reply agent - Azure deployment script
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1 -CopilotModel gpt-5.6-luna
#   powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1 -AlertEmail you@example.com
#   powershell -ExecutionPolicy Bypass -File .\scripts\deploy.ps1 -NoBudget   (if the subscription does not support budgets)
# Requirements: az login done, GHCR image is public.

param(
    [string]$ResourceGroup = 'shop-reply-rg',
    [string]$Location = 'koreacentral',
    [string]$Image = 'ghcr.io/isp829/shop-reply-agent:latest',
    [string]$AlertEmail = '',
    [string]$CopilotModel = '',
    [switch]$NoBudget
)

$ErrorActionPreference = 'Stop'

function Invoke-Az {
    param([string[]]$Arguments)
    $output = & az @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('az command failed: az ' + ($Arguments[0..2] -join ' ')) }
    return $output
}

$template = Join-Path $PSScriptRoot '..\infra\main.bicep'

Write-Host ('1/4 Resource group: ' + $ResourceGroup + ' (' + $Location + ')')
Invoke-Az @('group', 'create', '-n', $ResourceGroup, '-l', $Location, '-o', 'none') | Out-Null

Write-Host '2/4 Signed-in account'
$me = Invoke-Az @('ad', 'signed-in-user', 'show', '--query', 'id', '-o', 'tsv')

Write-Host '3/4 Bicep deployment (takes a few minutes)'
$budget = 'true'
if ($NoBudget) { $budget = 'false' }
$deployArgs = @(
    'deployment', 'group', 'create',
    '-g', $ResourceGroup,
    '-f', $template,
    '-p', ('containerImage=' + $Image), ('deployerPrincipalId=' + $me), ('alertEmail=' + $AlertEmail),
    ('copilotModel=' + $CopilotModel), ('deployBudget=' + $budget),
    '--query', 'properties.outputs', '-o', 'json'
)
$json = Invoke-Az $deployArgs
$out = ($json -join "`n") | ConvertFrom-Json

Write-Host '4/4 Restart web app'
Invoke-Az @('webapp', 'restart', '-n', $out.webAppName.value, '-g', $ResourceGroup) | Out-Null

Write-Host ''
Write-Host 'Deployment complete'
Write-Host ('  Web app:    ' + $out.url.value)
Write-Host ('  Web app name: ' + $out.webAppName.value)
Write-Host ('  Key Vault:  ' + $out.keyVaultName.value)
Write-Host ('  Table URL:  ' + $out.storageTableUrl.value)
Write-Host ''
Write-Host 'Next: add secrets GITHUB-TOKEN and ADMIN-PASSWORD to the Key Vault in the Azure portal, then restart the web app:'
Write-Host ('  az webapp restart -n ' + $out.webAppName.value + ' -g ' + $ResourceGroup)
Write-Host ''
Write-Host 'Local run environment variables:'
Write-Host ('  $env:KEY_VAULT_URL = ''' + $out.keyVaultUri.value + '''')
Write-Host ('  $env:STORAGE_TABLE_URL = ''' + $out.storageTableUrl.value + '''')
