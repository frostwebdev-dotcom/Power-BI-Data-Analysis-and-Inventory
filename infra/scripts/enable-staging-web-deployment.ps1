<# Extend the existing GitHub staging identity to the web repository and app. #>
param(
    [string]$Subscription = 'e3a5c050-b241-4488-83ec-6473da22f05a',
    [string]$ResourceGroup = 'rg-prms-staging',
    [string]$Principal = '31f07899-ae59-464f-81e0-7a5f073f2c15'
)
$ErrorActionPreference = 'Stop'
$acrScope = "/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.ContainerRegistry/registries/acrprmsstaginggs"
$webScope = "/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.App/containerApps/ca-prms-web-staging"
$raw = & az role assignment list --subscription $Subscription --scope $acrScope --output json
if ($LASTEXITCODE -ne 0) { throw 'Could not read registry role assignments.' }
$assignments = @((($raw -join "`n") | ConvertFrom-Json) | Where-Object {
    $_.principalId -eq $Principal -and $_.scope -eq $acrScope -and
    $_.roleDefinitionName -eq 'Container Registry Repository Writer'
})
if ($assignments.Count -ne 1) { throw 'Expected one existing registry Writer assignment for the GitHub identity.' }
$existing = $assignments[0]
if (-not $existing.condition -or $existing.condition -notmatch "StringEqualsIgnoreCase\s+'prms-api'") {
    throw 'The existing assignment differs from the repository-scoped API grant. Review it before changing permissions.'
}
$condition = "((!(ActionMatches{'Microsoft.ContainerRegistry/registries/repositories/content/read'}) AND !(ActionMatches{'Microsoft.ContainerRegistry/registries/repositories/content/write'}) AND !(ActionMatches{'Microsoft.ContainerRegistry/registries/repositories/metadata/read'}) AND !(ActionMatches{'Microsoft.ContainerRegistry/registries/repositories/metadata/write'})) OR ((@Request[Microsoft.ContainerRegistry/registries/repositories:name] StringEqualsIgnoreCase 'prms-api') OR (@Request[Microsoft.ContainerRegistry/registries/repositories:name] StringEqualsIgnoreCase 'prms-web')))"
$assignment = @{
    id = $existing.id; name = $existing.name
    principalId = $existing.principalId; principalType = 'ServicePrincipal'
    roleDefinitionId = $existing.roleDefinitionId; scope = $existing.scope
    condition = $condition; conditionVersion = '2.0'
    description = 'GitHub staging image uploads to prms-api and prms-web only'
}
# Update the existing assignment: another grant of the same role at the same
# scope can conflict or return the old condition without granting web access.
$bodyFile = Join-Path $env:TEMP ("prms-writer-condition-" + [guid]::NewGuid() + '.json')
try {
    [System.IO.File]::WriteAllText($bodyFile, ($assignment | ConvertTo-Json -Depth 20), [System.Text.UTF8Encoding]::new($false))
    & az role assignment update --subscription $Subscription --role-assignment $bodyFile --output none
    if ($LASTEXITCODE -ne 0) { throw 'Could not update the registry Writer condition.' }
} finally {
    Remove-Item -LiteralPath $bodyFile -Force -ErrorAction SilentlyContinue
}
& az role assignment create --subscription $Subscription --assignee-object-id $Principal --assignee-principal-type ServicePrincipal --role 'Container Apps Contributor' --scope $webScope --output none
if ($LASTEXITCODE -ne 0) { throw 'Registry access updated, but web app deployment permission could not be granted.' }
Write-Output 'GitHub staging permissions configured for the API and web repositories and web app.'
