# 쇼핑몰 문의 응대 에이전트 배포 스크립트
# 사용 예:
#   ./scripts/deploy.ps1
#   ./scripts/deploy.ps1 -AlertEmail you@example.com
#   ./scripts/deploy.ps1 -NoBudget        (예산 리소스를 지원하지 않는 구독일 때)
# 사전 조건: az login 완료, GHCR 이미지가 Public

param(
    [string]$ResourceGroup = "shop-reply-rg",
    [string]$Location = "koreacentral",
    [string]$Image = "ghcr.io/isp829/shop-reply-agent:latest",
    [string]$AlertEmail = "",
    [string]$CopilotModel = "",
    [switch]$NoBudget
)

$ErrorActionPreference = "Stop"

function Invoke-Az {
    param([string[]]$Arguments)
    $output = & az @Arguments
    if ($LASTEXITCODE -ne 0) { throw "az $($Arguments[0..1] -join ' ') 실패" }
    return $output
}

$template = Join-Path $PSScriptRoot "..\infra\main.bicep"

Write-Host "1/4 리소스 그룹 확인: $ResourceGroup ($Location)"
Invoke-Az @("group", "create", "-n", $ResourceGroup, "-l", $Location, "-o", "none") | Out-Null

Write-Host "2/4 배포하는 계정 확인"
$me = Invoke-Az @("ad", "signed-in-user", "show", "--query", "id", "-o", "tsv")

Write-Host "3/4 Bicep 배포 (몇 분 걸립니다)"
$budget = if ($NoBudget) { "false" } else { "true" }
$json = Invoke-Az @(
    "deployment", "group", "create",
    "-g", $ResourceGroup,
    "-f", $template,
    "-p", "containerImage=$Image", "deployerPrincipalId=$me", "alertEmail=$AlertEmail",
    "copilotModel=$CopilotModel", "deployBudget=$budget",
    "--query", "properties.outputs", "-o", "json"
)
$out = ($json -join "`n") | ConvertFrom-Json

Write-Host "4/4 앱 재시작"
Invoke-Az @("webapp", "restart", "-n", $out.webAppName.value, "-g", $ResourceGroup) | Out-Null

Write-Host ""
Write-Host "배포 완료"
Write-Host "  웹앱:       $($out.url.value)"
Write-Host "  Key Vault:  $($out.keyVaultName.value)"
Write-Host "  Table URL:  $($out.storageTableUrl.value)"
Write-Host ""
Write-Host "Key Vault에 GITHUB-TOKEN, ADMIN-PASSWORD 비밀이 없으면 포털에서 추가한 뒤 앱을 다시 시작하세요."
Write-Host "로컬 실행용 환경변수:"
Write-Host "  `$env:KEY_VAULT_URL=`"$($out.keyVaultUri.value)`""
Write-Host "  `$env:STORAGE_TABLE_URL=`"$($out.storageTableUrl.value)`""
