$ErrorActionPreference = 'Stop'
$env:TEMP = [System.IO.Path]::GetTempPath()
$global:initial = @{
    id = '/subscriptions/test/resourceGroups/rg-prms-staging/providers/Microsoft.App/containerApps/ca-prms-api-staging'
    properties = @{
        managedEnvironmentId = '/subscriptions/test/resourceGroups/rg-prms-staging/providers/Microsoft.App/managedEnvironments/cae-prms-staging'
        template = @{
            revisionSuffix = 'old'
            scale = @{ minReplicas = 1; maxReplicas = 1 }
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
function global:az {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'rest') {
        $index = [array]::IndexOf($args, '--body')
        $file = ($args[$index + 1]).Substring(1)
        $global:captured = Get-Content -Raw $file | ConvertFrom-Json
        $global:patchCount++
    } elseif ($args[1] -eq 'env') {
        'prms-raw'
    } elseif ($args -contains '--query') {
        '{}'
    } else {
        $global:initial | ConvertTo-Json -Depth 100
    }
}
$script = Join-Path $PSScriptRoot '../scripts/attach-staging-raw-storage.ps1'
& $script
$template = $global:captured.properties.template
if ($template.containers[0].image -ne 'registry/prms-api:reviewed') { throw 'Image was replaced' }
if ($template.containers[0].env[1].secretRef -ne 'database-url') { throw 'Secret reference was changed' }
if ($template.volumes.Count -ne 2 -or $template.containers[0].volumeMounts.Count -ne 2) { throw 'Existing mounts were lost' }
if ($template.scale.maxReplicas -ne 1 -or $template.PSObject.Properties.Name -contains 'revisionSuffix') { throw 'Incorrect template patch' }
if ($template.volumes[1].mountOptions -ne 'dir_mode=0770,file_mode=0660,uid=10001,gid=10001') { throw 'Incorrect file permissions' }
if ($template.containers[0].volumeMounts[1].mountPath -ne '/storage/raw') { throw 'Incorrect raw path' }
$global:initial.properties.template.containers[0].volumeMounts = @(@{ volumeName = 'existing'; mountPath = '/storage' })
$refused = $false
try { & $script } catch { $refused = $_.Exception.Message -like '*already exists*' }
if (-not $refused -or $global:patchCount -ne 1) { throw 'Did not protect an existing storage mount' }
Write-Output 'Storage attachment preserves configuration and refuses an existing parent mount.'
