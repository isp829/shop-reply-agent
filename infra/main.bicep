// 쇼핑몰 문의 응대 에이전트 인프라 전체 정의
// 배포: scripts/deploy.ps1 (az deployment group create)

@description('리소스 위치')
param location string = resourceGroup().location

@description('리소스 이름 접두사 (소문자, 숫자)')
@maxLength(10)
param namePrefix string = 'shopreply'

@description('실행할 컨테이너 이미지')
param containerImage string

@description('배포하는 사용자의 개체 ID. 로컬 개발용으로 Key Vault와 Table 권한을 준다. 비워 두면 건너뛴다.')
param deployerPrincipalId string = ''

@description('경보를 받을 이메일. 비워 두면 경보 규칙만 만들고 알림은 보내지 않는다.')
param alertEmail string = ''

@description('Copilot 모델 이름. 비워 두면 기본 모델을 쓴다.')
param copilotModel string = ''

@description('리소스 그룹 월 예산 알림을 만들지 여부')
param deployBudget bool = true

@description('월 예산 (USD)')
param budgetAmount int = 30

@description('예산 시작일 (해당 월 1일)')
param budgetStartDate string = utcNow('yyyy-MM-01')

var suffix = take(uniqueString(resourceGroup().id), 6)
var storageName = toLower('st${namePrefix}${suffix}')
var keyVaultName = 'kv-${namePrefix}-${suffix}'
var planName = 'plan-${namePrefix}-${suffix}'
var webAppName = 'app-${namePrefix}-${suffix}'

// 기본 제공 역할 ID
var roleStorageTableDataContributor = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var roleKeyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var roleKeyVaultSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

// ---------- 관측 ----------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${namePrefix}-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 } // 비용 상한
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${namePrefix}-${suffix}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// ---------- 데이터 ----------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false // 계정 키 접근 차단, Managed Identity만 허용
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource ordersTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'orders'
}

resource inquiriesTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'inquiries'
}

// ---------- 비밀 정보 ----------
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// ---------- 호스팅 ----------
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: { name: 'B1', tier: 'Basic' }
  properties: { reserved: true }
}

resource web 'Microsoft.Web/sites@2023-12-01' = {
  name: webAppName
  location: location
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOCKER|${containerImage}'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/healthz'
      appSettings: [
        { name: 'WEBSITES_PORT', value: '8000' }
        { name: 'WEBSITES_CONTAINER_START_TIME_LIMIT', value: '600' }
        { name: 'KEY_VAULT_URL', value: keyVault.properties.vaultUri }
        { name: 'STORAGE_TABLE_URL', value: storage.properties.primaryEndpoints.table }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'COPILOT_MODEL', value: copilotModel }
      ]
    }
  }
}

resource webLogs 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: web
  name: 'logs'
  properties: {
    applicationLogs: { fileSystem: { level: 'Information' } }
    httpLogs: { fileSystem: { enabled: true, retentionInDays: 3, retentionInMb: 35 } }
    detailedErrorMessages: { enabled: true }
    failedRequestsTracing: { enabled: false }
  }
}

resource webDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-log-analytics'
  scope: web
  properties: {
    workspaceId: logs.id
    logs: [
      { category: 'AppServiceConsoleLogs', enabled: true }
      { category: 'AppServiceHTTPLogs', enabled: true }
      { category: 'AppServicePlatformLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ---------- 권한 (최소 권한) ----------
resource webTableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, web.id, roleStorageTableDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageTableDataContributor)
    principalId: web.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource webSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, web.id, roleKeyVaultSecretsUser)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKeyVaultSecretsUser)
    principalId: web.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerTableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(storage.id, deployerPrincipalId, roleStorageTableDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageTableDataContributor)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

resource deployerSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(keyVault.id, deployerPrincipalId, roleKeyVaultSecretsOfficer)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleKeyVaultSecretsOfficer)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

// ---------- 경보 ----------
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(alertEmail)) {
  name: 'ag-${namePrefix}-${suffix}'
  location: 'global'
  properties: {
    groupShortName: 'shopreply'
    enabled: true
    emailReceivers: [
      { name: 'operator', emailAddress: alertEmail, useCommonAlertSchema: true }
    ]
  }
}

var alertActions = empty(alertEmail) ? [] : [ { actionGroupId: actionGroup.id } ]

resource http5xxAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-${webAppName}-5xx'
  location: 'global'
  properties: {
    description: '5분 동안 서버 오류(5xx)가 5건을 넘으면 알린다'
    severity: 2
    enabled: true
    scopes: [ web.id ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'http5xx'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'Http5xx'
          operator: 'GreaterThan'
          threshold: 5
          timeAggregation: 'Total'
        }
      ]
    }
    actions: alertActions
  }
}

resource healthAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-${webAppName}-health'
  location: 'global'
  properties: {
    description: '상태 확인(/healthz) 성공률이 100% 미만이면 알린다'
    severity: 1
    enabled: true
    scopes: [ web.id ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'health'
          criterionType: 'StaticThresholdCriterion'
          metricName: 'HealthCheckStatus'
          operator: 'LessThan'
          threshold: 100
          timeAggregation: 'Average'
        }
      ]
    }
    actions: alertActions
  }
}

// ---------- 비용 ----------
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = if (deployBudget) {
  name: 'budget-${namePrefix}'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: empty(alertEmail) ? [] : [ alertEmail ]
        contactRoles: [ 'Owner' ]
      }
    }
  }
}

output webAppName string = web.name
output url string = 'https://${web.properties.defaultHostName}'
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output storageTableUrl string = storage.properties.primaryEndpoints.table
output storageAccountName string = storage.name
