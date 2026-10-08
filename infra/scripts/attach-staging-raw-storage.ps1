<#
Attach the already provisioned environment storage to the API's raw directory.
Creates a revision, preserving the current image, environment, scale and mounts.
Before running: wait for sync/import jobs to finish and preserve any existing
ephemeral raw files; the new share hides the previous container-local directory.
#>
param(
    [string]$Subscription = 'e3a5c050-b241-4488-83ec-6473da22f05a',
    [string]$ResourceGroup = 'rg-prms-staging',
    [string]$AppName = 'ca-prms-api-staging',
    [string]$ContainerName = 'prms-api',
    [string]$EnvironmentStorageName = 'prms-raw',
    [ValidateRange(1, 180)][int]$MaxPollAttempts = 90
)
$ErrorActionPreference = 'Stop'

# Read and patch with one schema. Azure CLI's current model can include scaling
# fields that the older 2024-03-01 PATCH endpoint rejects.
$appId = "/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.App/containerApps/$AppName"
$resourceUrl = "https://management.azure.com${appId}?api-version=2025-07-01"
$raw = & az rest --subscription $Subscription --method get --url $resourceUrl --output json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the Container App.' }
$app = ($raw -join "`n") | ConvertFrom-Json
if ($app.properties.provisioningState -and $app.properties.provisioningState -notin @('Succeeded', 'Failed', 'Canceled')) {
    throw 'A Container App update is already in progress. Wait for it before attaching storage.'
}
$previousRevision = $app.properties.latestRevisionName
$environmentId = $app.properties.environmentId
if (-not $environmentId) { $environmentId = $app.properties.managedEnvironmentId }
$environmentName = ($environmentId -split '/')[-1]
$storage = & az containerapp env storage show --subscription $Subscription --resource-group $ResourceGroup --name $environmentName --storage-name $EnvironmentStorageName --query name --output tsv
if ($LASTEXITCODE -ne 0 -or -not $storage) { throw 'Provision the environment file share before attaching it.' }
$template = $app.properties.template
$container = @($template.containers | Where-Object { $_.name -eq $ContainerName })
if ($container.Count -ne 1) { throw 'Expected exactly one named API container.' }
if (@($container[0].env | Where-Object { $_.name -eq 'STORAGE_RAW_DIR' -and $_.value -ne '/storage/raw' }).Count) {
    throw 'STORAGE_RAW_DIR differs from /storage/raw. Review the mount path before applying.'
}
$volumeName = 'prms-raw-persistent'
$existingMount = @($container[0].volumeMounts | Where-Object {
    $_.mountPath -and ('/storage/raw' -eq $_.mountPath -or '/storage/raw'.StartsWith($_.mountPath.TrimEnd('/') + '/'))
})
if ($existingMount.Count) { throw 'A raw storage mount already exists; inspect it rather than replacing it.' }
if (@($template.volumes | Where-Object { $_.name -eq $volumeName }).Count) {
    throw 'The proposed volume name already exists; inspect the configuration.'
}
$volumes = @($template.volumes | Where-Object { $null -ne $_ }) + @(@{
    name = $volumeName; storageType = 'AzureFile'; storageName = $EnvironmentStorageName
    mountOptions = 'dir_mode=0770,file_mode=0660,uid=10001,gid=10001'
})
$template | Add-Member -NotePropertyName volumes -NotePropertyValue $volumes -Force
$mounts = @($container[0].volumeMounts | Where-Object { $null -ne $_ }) + @(@{
    volumeName = $volumeName; mountPath = '/storage/raw'
})
$container[0] | Add-Member -NotePropertyName volumeMounts -NotePropertyValue $mounts -Force
$template.PSObject.Properties.Remove('revisionSuffix')
# az rest returns immediately on HTTP 202 and does not expose the operation
# headers to the script. Use the current CLI account's token in memory so we can
# track the actual operation, including errors that never reach system logs.
$token = & az account get-access-token --subscription $Subscription --resource 'https://management.azure.com/' --query accessToken --output tsv
if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'Could not authenticate the storage update using the current Azure CLI account.' }
$headers = @{ Authorization = 'Bearer ' + ($token -join '').Trim() }
function Invoke-PrmsArmRequest {
    param([string]$Method, [string]$Uri, [byte[]]$Body)
    $target = [uri]$Uri
    if ($target.Scheme -ne 'https' -or $target.Host -ne 'management.azure.com' -or $target.UserInfo) {
        throw 'Azure returned an unexpected operation URL; refusing to send credentials.'
    }
    $parameters = @{
        UseBasicParsing = $true; Method = $Method; Uri = $Uri
        Headers = $headers; TimeoutSec = 60; MaximumRedirection = 0
        ErrorAction = 'Stop'
    }
    if ($null -ne $Body) { $parameters.Body = $Body; $parameters.ContentType = 'application/json' }
    try { Invoke-WebRequest @parameters } catch {
        $detail = $_.ErrorDetails.Message
        if (-not $detail) { $detail = $_.Exception.Message }
        throw "Azure $Method request failed: $detail"
    }
}
try {
    $body = @{ location = $app.location; properties = @{ template = $template } } | ConvertTo-Json -Depth 100
    $response = Invoke-PrmsArmRequest -Method Patch -Uri $resourceUrl -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
    $operationUrl = [string]$response.Headers['Azure-AsyncOperation']
    $usesAsyncStatus = [bool]$operationUrl
    if (-not $operationUrl) { $operationUrl = [string]$response.Headers['Location'] }
    if ($response.StatusCode -notin @(200, 202)) { throw "Unexpected PATCH status: $($response.StatusCode)" }
    if ($response.StatusCode -eq 202 -and -not $operationUrl) {
        throw 'Azure accepted the update without a tracking URL. Inspect the Activity Log before retrying.'
    }
    if ($operationUrl) {
        Write-Output "Storage update operation: $operationUrl"
        Write-Output "Correlation ID: $($response.Headers['x-ms-correlation-request-id'])"
        $finished = $false
        $deadline = [datetime]::UtcNow.AddMinutes(15)
        for ($attempt = 1; $attempt -le $MaxPollAttempts -and [datetime]::UtcNow -lt $deadline; $attempt++) {
            $delay = 10
            $retryAfter = 0
            if ([int]::TryParse([string]$response.Headers['Retry-After'], [ref]$retryAfter)) {
                $delay = [math]::Min(30, [math]::Max(1, $retryAfter))
            }
            Start-Sleep -Seconds $delay
            $response = Invoke-PrmsArmRequest -Method Get -Uri $operationUrl
            $result = if ($response.Content) { $response.Content | ConvertFrom-Json } else { $null }
            $status = $result.status
            if (-not $status) { $status = $result.properties.provisioningState }
            Write-Output "Operation check ${attempt}: HTTP $($response.StatusCode); status=$status"
            if ($result.error -or $status -in @('Failed', 'Canceled', 'Cancelled')) {
                $errorJson = $result.error | ConvertTo-Json -Depth 20 -Compress
                throw "Storage update operation failed: $status $errorJson. Operation: $operationUrl"
            }
            if ($status -eq 'Succeeded' -or (-not $usesAsyncStatus -and $response.StatusCode -in @(200, 204) -and -not $status)) {
                $finished = $true
                break
            }
        }
        if (-not $finished) { throw "Storage update has not completed. Check this operation before retrying: $operationUrl" }
    }
    # A successful operation still needs a new, ready revision with the mount.
    # In particular, do not interpret the old revision's stale Failed state as
    # the outcome of the operation we just submitted.
    for ($attempt = 1; $attempt -le $MaxPollAttempts; $attempt++) {
        $response = Invoke-PrmsArmRequest -Method Get -Uri $resourceUrl
        $updated = $response.Content | ConvertFrom-Json
        $properties = $updated.properties
        $mounted = @($properties.template.containers | Where-Object { $_.name -eq $ContainerName } |
            ForEach-Object { $_.volumeMounts } | Where-Object { $_.volumeName -eq $volumeName -and $_.mountPath -eq '/storage/raw' })
        $volume = @($properties.template.volumes | Where-Object {
            $_.name -eq $volumeName -and $_.storageType -eq 'AzureFile' -and $_.storageName -eq $EnvironmentStorageName
        })
        Write-Output "Mount check ${attempt}: state=$($properties.provisioningState); revision=$($properties.latestRevisionName)"
        if ($properties.latestRevisionName -ne $previousRevision -and $properties.provisioningState -in @('Failed', 'Canceled')) {
            throw "The storage revision failed: $($properties.provisioningError). Inspect its system logs."
        }
        if ($mounted.Count -eq 1 -and $volume.Count -eq 1 -and $properties.provisioningState -eq 'Succeeded' -and
            $properties.latestRevisionName -ne $previousRevision -and $properties.latestReadyRevisionName -eq $properties.latestRevisionName) {
            [pscustomobject]@{
                State = $properties.provisioningState; Revision = $properties.latestRevisionName
                ReadyRevision = $properties.latestReadyRevisionName; Volumes = $properties.template.volumes
                Mounts = $mounted
            } | ConvertTo-Json -Depth 20
            return
        }
        if ($attempt -lt $MaxPollAttempts) { Start-Sleep -Seconds 10 }
    }
    throw 'The operation finished, but a new ready revision with the raw storage mount was not verified. Inspect the app before retrying.'
} finally {
    $headers.Clear()
    Remove-Variable token, body -ErrorAction SilentlyContinue
}
