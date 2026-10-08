$ErrorActionPreference = 'Stop'
$env:TEMP = [System.IO.Path]::GetTempPath()
$global:initial = @{
    id = '/subscriptions/test/resourceGroups/rg-prms-staging/providers/Microsoft.App/containerApps/ca-prms-api-staging'
    location = 'eastus2'
    properties = @{
        provisioningState = 'Failed'
        latestRevisionName = 'old'
        latestReadyRevisionName = 'old'
        managedEnvironmentId = '/subscriptions/test/resourceGroups/rg-prms-staging/providers/Microsoft.App/managedEnvironments/cae-prms-staging'
        template = @{
            revisionSuffix = 'old'
            scale = @{
                minReplicas = 1; maxReplicas = 1
                cooldownPeriod = 350; pollingInterval = 35
                rules = @(@{ name = 'http'; http = @{ metadata = @{ concurrentRequests = '50' } } })
            }
            volumes = @(@{ name = 'cache'; storageType = 'EmptyDir' })
            containers = @(@{
                name = 'prms-api'; image = 'registry/prms-api:reviewed'
                env = @(@{ name = 'AUTH_BACKEND'; value = 'entra' }, @{ name = 'DATABASE_URL'; secretRef = 'database-url' })
                volumeMounts = @(@{ volumeName = 'cache'; mountPath = '/cache' })
            })
        }
    }
}
$global:patchCount = 0
$global:readUrl = $null
$global:patchUrl = $null
$global:scenario = 'delayed'
$global:operationReads = 0
$global:resourceReads = 0
$global:delays = @()
$operationUrl = 'https://management.azure.com/subscriptions/test/providers/Microsoft.App/locations/eastus2/containerappOperationResults/test?api-version=2025-07-01&track=true'
function global:az {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'rest') {
        $method = $args[[array]::IndexOf($args, '--method') + 1]
        $url = $args[[array]::IndexOf($args, '--url') + 1]
        if ($method -eq 'get') {
            $global:readUrl = $url
            $global:initial | ConvertTo-Json -Depth 100
            return
        }
        throw 'Unexpected REST method'
    } elseif ($args[1] -eq 'env') {
        if ($args[[array]::IndexOf($args, '--name') + 1] -ne 'cae-prms-staging') { throw 'Incorrect environment lookup' }
        'prms-raw'
    } elseif ($args[0] -eq 'account') {
        'mock-secret-token-never-print'
    } else {
        $global:initial | ConvertTo-Json -Depth 100
    }
}
function global:Start-Sleep { param([int]$Seconds) $global:delays += $Seconds }
function global:Invoke-WebRequest {
    [CmdletBinding()]
    param([switch]$UseBasicParsing, [string]$Method, [string]$Uri, $Headers,
        [int]$TimeoutSec, [int]$MaximumRedirection, [byte[]]$Body, [string]$ContentType)
    if ($Headers.Authorization -ne 'Bearer mock-secret-token-never-print') { throw 'CLI credentials not used' }
    if ($MaximumRedirection -ne 0) { throw 'Credentials could follow a redirect' }
    if ($Method -eq 'Patch') {
        if ($ContentType -ne 'application/json') { throw 'JSON content type not set' }
        $global:patchUrl = $Uri
        $global:captured = [System.Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json
        $global:patchCount++
        $global:operationReads = 0
        $global:resourceReads = 0
        if ($global:scenario -eq 'http-error') { throw 'HTTP 400: rejected template' }
        $responseHeaders = @{ 'Azure-AsyncOperation' = $operationUrl; 'Retry-After' = '2'; 'x-ms-correlation-request-id' = 'test-correlation' }
        if ($global:scenario -like 'location*') {
            $responseHeaders.Remove('Azure-AsyncOperation')
            $responseHeaders.Location = $operationUrl
        }
        if ($global:scenario -eq 'bad-url') { $responseHeaders['Azure-AsyncOperation'] = 'https://example.com/operation' }
        if ($global:scenario -in @('missing-url', 'synchronous')) { $responseHeaders.Remove('Azure-AsyncOperation') }
        $code = if ($global:scenario -eq 'synchronous') { 200 } else { 202 }
        return [pscustomobject]@{ StatusCode = $code; Headers = $responseHeaders; Content = '' }
    }
    if ($Uri -eq $operationUrl) {
        $global:operationReads++
        $status = if ($global:operationReads -eq 1 -or $global:scenario -eq 'pending') { 'InProgress' } else { 'Succeeded' }
        $operation = @{ status = $status }
        if ($global:scenario -eq 'failed') { $operation = @{ status = 'Failed'; error = @{ code = 'MountFailure'; message = 'Share cannot be mounted' } } }
        $code = 200
        if ($global:scenario -like 'location*') {
            $code = if ($global:operationReads -eq 1) { 202 } else { 204 }
            $operation = $null
        }
        return [pscustomobject]@{ StatusCode = $code; Headers = @{}; Content = ($operation | ConvertTo-Json -Depth 20) }
    }
    if ($Uri -ne $global:readUrl) { throw 'Unexpected URL' }
    $global:resourceReads++
    if ($global:resourceReads -eq 1 -or $global:scenario -eq 'stale') { $resource = $global:initial } else {
        $resource = @{ properties = @{
            provisioningState = 'Succeeded'; latestRevisionName = 'mounted'; latestReadyRevisionName = 'mounted'
            template = $global:captured.properties.template
        } }
        if ($global:scenario -eq 'revision-failed') { $resource.properties.provisioningState = 'Failed'; $resource.properties.provisioningError = 'Probe failure' }
        if ($global:scenario -eq 'not-ready') { $resource.properties.latestReadyRevisionName = 'old' }
    }
    [pscustomobject]@{ StatusCode = 200; Headers = @{}; Content = ($resource | ConvertTo-Json -Depth 100) }
}
$script = Join-Path $PSScriptRoot '../scripts/attach-staging-raw-storage.ps1'
$output = (& $script -Subscription test -MaxPollAttempts 3) -join "`n"
if ($output -match 'mock-secret-token-never-print' -or $output -notmatch '"ReadyRevision": "mounted"') { throw 'Unverified success or credentials printed' }
if ($global:operationReads -ne 2 -or $global:resourceReads -ne 2 -or $global:delays[0] -ne 2) { throw 'Did not wait for operation, Retry-After and resource consistency' }
$template = $global:captured.properties.template
if ($global:readUrl -ne $global:patchUrl -or -not $global:patchUrl.EndsWith('?api-version=2025-07-01')) { throw 'Read and patch API schemas differ' }
if ($global:captured.location -ne 'eastus2') { throw 'App location was not preserved' }
if ($template.scale.cooldownPeriod -ne 350 -or $template.scale.pollingInterval -ne 35 -or $template.scale.rules[0].http.metadata.concurrentRequests -ne '50') { throw 'Scaling settings were changed' }
if ($template.containers[0].image -ne 'registry/prms-api:reviewed') { throw 'Image was replaced' }
if ($template.containers[0].env[1].secretRef -ne 'database-url') { throw 'Secret reference was changed' }
if ($template.volumes.Count -ne 2 -or $template.containers[0].volumeMounts.Count -ne 2) { throw 'Existing mounts were lost' }
if ($template.scale.maxReplicas -ne 1 -or $template.PSObject.Properties.Name -contains 'revisionSuffix') { throw 'Incorrect template patch' }
if ($template.volumes[1].mountOptions -ne 'dir_mode=0770,file_mode=0660,uid=10001,gid=10001') { throw 'Incorrect file permissions' }
if ($template.containers[0].volumeMounts[1].mountPath -ne '/storage/raw') { throw 'Incorrect raw path' }
# New API responses can use environmentId instead of managedEnvironmentId.
$global:initial.properties.environmentId = $global:initial.properties.managedEnvironmentId
$global:initial.properties.Remove('managedEnvironmentId')
& $script -Subscription test -MaxPollAttempts 3 | Out-Null
if ($global:patchCount -ne 2) { throw 'New environment ID field was not supported' }
foreach ($case in @('location', 'synchronous')) {
    $global:scenario = $case
    & $script -Subscription test -MaxPollAttempts 3 | Out-Null
}
foreach ($case in @{
    failed = '*MountFailure*'; pending = '*has not completed*'; stale = '*was not verified*'
    'not-ready' = '*was not verified*'; 'revision-failed' = '*Probe failure*'
    'http-error' = '*HTTP 400*'; 'missing-url' = '*without a tracking URL*'; 'bad-url' = '*unexpected operation URL*'
}.GetEnumerator()) {
    $global:scenario = $case.Key
    $refused = $false
    try { & $script -Subscription test -MaxPollAttempts 3 | Out-Null } catch { $refused = $_.Exception.Message -like $case.Value }
    if (-not $refused) { throw "Failed to report scenario: $($case.Key)" }
}
$beforeRefusal = $global:patchCount
$global:initial.properties.provisioningState = 'InProgress'
$refused = $false
try { & $script -Subscription test | Out-Null } catch { $refused = $_.Exception.Message -like '*already in progress*' }
if (-not $refused -or $global:patchCount -ne $beforeRefusal) { throw 'Submitted overlapping updates' }
$global:initial.properties.provisioningState = 'Succeeded'
$global:initial.properties.template.containers[0].volumeMounts = @(@{ volumeName = 'existing'; mountPath = '/storage' })
$refused = $false
try { & $script -Subscription test | Out-Null } catch { $refused = $_.Exception.Message -like '*already exists*' }
if (-not $refused -or $global:patchCount -ne $beforeRefusal) { throw 'Did not protect an existing storage mount' }
Write-Output 'Storage attachment preserves configuration, tracks operation errors, and requires a new ready mounted revision.'
