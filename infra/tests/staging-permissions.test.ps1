$ErrorActionPreference = 'Stop'
$env:TEMP = [System.IO.Path]::GetTempPath()
$scope = '/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.ContainerRegistry/registries/acrprmsstaginggs'
$global:grant = @{
    id = 'existing-assignment'; name = 'existing-guid'; principalId = 'github-principal'
    roleDefinitionId = 'writer-role'; roleDefinitionName = 'Container Registry Repository Writer'
    scope = $scope; condition = "@Request[Microsoft.ContainerRegistry/registries/repositories:name] StringEqualsIgnoreCase 'prms-api'"
}
$global:updated = 0
$global:created = 0
function global:az {
    $global:LASTEXITCODE = 0
    if ($args[2] -eq 'list') {
        @($global:grant) | ConvertTo-Json -Depth 20 -AsArray
    } elseif ($args[2] -eq 'update') {
        $index = [array]::IndexOf($args, '--role-assignment')
        $global:captured = Get-Content -Raw $args[$index + 1] | ConvertFrom-Json
        $global:updated++
    } elseif ($args[2] -eq 'create') {
        if ($args -contains 'Container Registry Repository Writer') { throw 'Attempted a duplicate registry grant' }
        $index = [array]::IndexOf($args, '--scope')
        if ($args[$index + 1] -ne '/subscriptions/test/resourceGroups/test-rg/providers/Microsoft.App/containerApps/ca-prms-web-staging') {
            throw 'Wrong deployment scope'
        }
        if ($args -notcontains 'Container Apps Contributor') { throw 'Wrong deployment role' }
        $global:created++
    } else { throw 'Unexpected Azure command' }
}
$script = Join-Path $PSScriptRoot '../scripts/enable-staging-web-deployment.ps1'
& $script -Subscription test -ResourceGroup test-rg -Principal github-principal
if ($global:captured.id -ne 'existing-assignment' -or $global:captured.principalId -ne 'github-principal' -or $global:captured.roleDefinitionId -ne 'writer-role' -or $global:captured.scope -ne $scope) {
    throw 'Existing registry assignment identity changed'
}
if ($global:captured.condition -notmatch "StringEqualsIgnoreCase 'prms-api'" -or $global:captured.condition -notmatch "StringEqualsIgnoreCase 'prms-web'" -or $global:captured.conditionVersion -ne '2.0') {
    throw 'Repository scope was not preserved'
}
$global:grant.condition = $null
$refused = $false
try { & $script -Subscription test -ResourceGroup test-rg -Principal github-principal } catch {
    $refused = $_.Exception.Message -like '*differs*'
}
if (-not $refused -or $global:updated -ne 1 -or $global:created -ne 1) { throw 'Unexpected role assignments were not protected' }
Write-Output 'Staging permission setup preserves the API grant and only adds web deployment access.'
