<#
One-time local Gmail setup. Requires Python 3.12+ with backend dependencies.
The Google client JSON and resulting runtime JSON stay on the operator's PC.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ClientConfigPath,
    [Parameter(Mandatory = $true)][string]$Mailbox,
    [Parameter(Mandatory = $true)][string]$OrganizationSlug,
    [string]$OutputPath = "",
    [string]$PythonExecutable = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$gmailRedirectUri = "http://127.0.0.1:8765/oauth/callback"
try {
    if ((Get-Item -LiteralPath $ClientConfigPath).Length -gt 65536) { throw "Too large." }
    $gmailClientConfig = Get-Content -LiteralPath $ClientConfigPath -Encoding UTF8 -Raw | ConvertFrom-Json
}
catch { throw "The Google client JSON could not be read or parsed." }
if ($null -eq $gmailClientConfig.PSObject.Properties["web"]) {
    throw "Use the downloaded Google Web application OAuth client JSON."
}
$gmailClient = $gmailClientConfig.web
if ($null -eq $gmailClient) { throw "The Google client JSON needs a web client object." }
foreach ($gmailField in @("client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris")) {
    if ($null -eq $gmailClient.PSObject.Properties[$gmailField]) {
        throw "The Google client JSON is missing a required web-client field."
    }
}
if ($gmailClient.auth_uri -ne "https://accounts.google.com/o/oauth2/auth" -and
    $gmailClient.auth_uri -ne "https://accounts.google.com/o/oauth2/v2/auth") {
    throw "The authorization endpoint must be Google's official endpoint."
}
if ($gmailClient.token_uri -ne "https://oauth2.googleapis.com/token") {
    throw "The token endpoint must be Google's official endpoint."
}
if (@($gmailClient.redirect_uris) -notcontains $gmailRedirectUri) {
    throw "Register http://127.0.0.1:8765/oauth/callback in this Google web client first."
}
if ([string]::IsNullOrWhiteSpace([string]$gmailClient.client_id) -or
    [string]::IsNullOrWhiteSpace([string]$gmailClient.client_secret)) {
    throw "The Google web client needs its client ID and secret."
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $gmailPrivateDirectory = Join-Path $env:LOCALAPPDATA "PRMS\GmailSetup"
    $OutputPath = Join-Path $gmailPrivateDirectory ("gmail-runtime-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
}
$gmailSettings = @{
    VENDOR_MAIL_PROVIDER = "gmail"
    VENDOR_MAIL_ENABLED = "false"
    VENDOR_MAIL_GMAIL_CLIENT_ID = [string]$gmailClient.client_id
    VENDOR_MAIL_GMAIL_CLIENT_SECRET = [string]$gmailClient.client_secret
    VENDOR_MAIL_MAILBOX = $Mailbox
    VENDOR_MAIL_ORGANIZATION_SLUG = $OrganizationSlug
    VENDOR_MAIL_GMAIL_LABEL_ID = "INBOX"
}
$gmailPriorSettings = @{}
$gmailBackendDirectory = Join-Path $PSScriptRoot "..\..\backend"
foreach ($gmailName in $gmailSettings.Keys) {
    $gmailPriorSettings[$gmailName] = [Environment]::GetEnvironmentVariable($gmailName, "Process")
}
try {
    foreach ($gmailName in $gmailSettings.Keys) {
        [Environment]::SetEnvironmentVariable($gmailName, $gmailSettings[$gmailName], "Process")
    }
    Push-Location $gmailBackendDirectory
    try {
        & $PythonExecutable -m app.cli.vendor_mail_authorize --output $OutputPath
        if ($LASTEXITCODE -ne 0) {
            throw "Gmail authorization did not complete. Check the safe error code above."
        }
        Write-Host ("Private runtime settings: " + $OutputPath)
    }
    finally { Pop-Location }
}
finally {
    foreach ($gmailName in $gmailSettings.Keys) {
        [Environment]::SetEnvironmentVariable($gmailName, $gmailPriorSettings[$gmailName], "Process")
    }
    $gmailClient = $null
    $gmailClientConfig = $null
}
