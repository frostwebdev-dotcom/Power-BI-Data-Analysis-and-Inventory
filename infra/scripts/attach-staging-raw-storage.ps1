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
    [string]$EnvironmentStorageName = 'prms-raw'
)
$ErrorActionPreference = 'Stop'

# Read and patch with one schema. Azure CLI's current model can include scaling
# fields that the older 2024-03-01 PATCH endpoint rejects.
$appId = "/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.App/containerApps/$AppName"
$resourceUrl = "https://management.azure.com${appId}?api-version=2025-07-01"
$raw = & az rest --subscription $Subscription --method get --url $resourceUrl --output json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the Container App.' }
$app = ($raw -join "`n") | ConvertFrom-Json
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
$bodyFile = Join-Path $env:TEMP ("prms-storage-patch-" + [guid]::NewGuid() + '.json')
try {
    $body = @{ location = $app.location; properties = @{ template = $template } } | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText($bodyFile, $body, [System.Text.UTF8Encoding]::new($false))
    & az rest --subscription $Subscription --method patch --url $resourceUrl --body "@$bodyFile" --output none
    if ($LASTEXITCODE -ne 0) { throw 'Could not apply the storage mount.' }
} finally {
    Remove-Item -LiteralPath $bodyFile -Force -ErrorAction SilentlyContinue
}
& az containerapp show --subscription $Subscription --resource-group $ResourceGroup --name $AppName --query '{Revision:properties.latestRevisionName,Volumes:properties.template.volumes,Mounts:properties.template.containers[].volumeMounts}' --output json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the updated mount.' }
