$ErrorActionPreference = 'Stop'
$env:TEMP = [System.IO.Path]::GetTempPath()
$global:initial = @{
    id = '/subscriptions/test/resourceGroups/rg-prms-staging/providers/Microsoft.App/containerApps/ca-prms-api-staging'
    location = 'eastus2'
    properties = @{
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
        if ($method -ne 'patch') { throw 'Unexpected REST method' }
        $global:patchUrl = $url
        $index = [array]::IndexOf($args, '--body')
        $file = ($args[$index + 1]).Substring(1)
        $global:captured = Get-Content -Raw $file | ConvertFrom-Json
        $global:patchCount++
    } elseif ($args[1] -eq 'env') {
        if ($args[[array]::IndexOf($args, '--name') + 1] -ne 'cae-prms-staging') { throw 'Incorrect environment lookup' }
        'prms-raw'
    } elseif ($args -contains '--query') {
        '{}'
    } else {
        $global:initial | ConvertTo-Json -Depth 100
    }
}
$script = Join-Path $PSScriptRoot '../scripts/attach-staging-raw-storage.ps1'
& $script -Subscription test
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
& $script -Subscription test
if ($global:patchCount -ne 2) { throw 'New environment ID field was not supported' }
$global:initial.properties.template.containers[0].volumeMounts = @(@{ volumeName = 'existing'; mountPath = '/storage' })
$refused = $false
try { & $script -Subscription test } catch { $refused = $_.Exception.Message -like '*already exists*' }
if (-not $refused -or $global:patchCount -ne 2) { throw 'Did not protect an existing storage mount' }
Write-Output 'Storage attachment preserves configuration and refuses an existing parent mount.'
