# Windows PowerShell 5.1: no browser, Google requests or real credentials.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$gmailLauncher = Join-Path $PSScriptRoot "..\scripts\connect-gmail.ps1"
$gmailParseTokens = $null
$gmailParseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    $gmailLauncher, [ref]$gmailParseTokens, [ref]$gmailParseErrors
)
if ($gmailParseErrors.Count -gt 0) { throw "Gmail launcher syntax is invalid." }
function Assert-Gmail([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
$gmailTestDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("prms-gmail-" + [guid]::NewGuid().ToString("N"))
[void][System.IO.Directory]::CreateDirectory($gmailTestDirectory)
$gmailConfigFile = Join-Path $gmailTestDirectory "client.json"
$gmailOutputFile = Join-Path $gmailTestDirectory "runtime.json"
$gmailSavedClientSecret = [Environment]::GetEnvironmentVariable("VENDOR_MAIL_GMAIL_CLIENT_SECRET", "Process")
$gmailSavedEnabled = [Environment]::GetEnvironmentVariable("VENDOR_MAIL_ENABLED", "Process")
$gmailInitialLocation = (Get-Location).Path
$global:gmailTestNativeCalls = 0
$global:gmailTestNativeFailure = $false
function global:Invoke-GmailTestPython {
    $global:gmailTestNativeCalls += 1
    Assert-Gmail ($env:VENDOR_MAIL_GMAIL_CLIENT_SECRET -eq "synthetic-client-secret") "The downloaded client was not passed privately."
    Assert-Gmail ($env:VENDOR_MAIL_ENABLED -eq "false") "Setup must leave polling disabled."
    Assert-Gmail ($env:VENDOR_MAIL_MAILBOX -eq "inventory@example.test") "Wrong mailbox."
    if ($global:gmailTestNativeFailure) { $global:LASTEXITCODE = 2 } else { $global:LASTEXITCODE = 0 }
}
$gmailWebClient = @{
    client_id = "synthetic.apps.googleusercontent.com"
    client_secret = "synthetic-client-secret"
    auth_uri = "https://accounts.google.com/o/oauth2/auth"
    token_uri = "https://oauth2.googleapis.com/token"
    redirect_uris = @("http://127.0.0.1:8765/oauth/callback")
}
try {
    [Environment]::SetEnvironmentVariable("VENDOR_MAIL_GMAIL_CLIENT_SECRET", "prior-synthetic-value", "Process")
    [Environment]::SetEnvironmentVariable("VENDOR_MAIL_ENABLED", "true", "Process")
    [System.IO.File]::WriteAllText($gmailConfigFile, (@{web=$gmailWebClient} | ConvertTo-Json -Depth 5))
    $gmailConsole = (& $gmailLauncher -ClientConfigPath $gmailConfigFile -Mailbox "inventory@example.test" -OrganizationSlug "example-org" -OutputPath $gmailOutputFile -PythonExecutable "Invoke-GmailTestPython" 6>&1 | Out-String)
    Assert-Gmail (-not $gmailConsole.Contains("synthetic-client-secret")) "Secret leaked into console output."
    Assert-Gmail ($env:VENDOR_MAIL_GMAIL_CLIENT_SECRET -eq "prior-synthetic-value") "Prior secret was not restored."
    Assert-Gmail ($env:VENDOR_MAIL_ENABLED -eq "true") "Prior polling setting was not restored."
    Assert-Gmail ((Get-Location).Path -eq $gmailInitialLocation) "Working directory was not restored."
    $global:gmailTestNativeFailure = $true
    $gmailFailed = $false
    try {
        & $gmailLauncher -ClientConfigPath $gmailConfigFile -Mailbox "inventory@example.test" -OrganizationSlug "example-org" -OutputPath $gmailOutputFile -PythonExecutable "Invoke-GmailTestPython"
    } catch { $gmailFailed = $true }
    Assert-Gmail $gmailFailed "A failed authorization must fail the launcher."
    Assert-Gmail ($env:VENDOR_MAIL_GMAIL_CLIENT_SECRET -eq "prior-synthetic-value") "Secret was not restored after failure."
    Assert-Gmail ((Get-Location).Path -eq $gmailInitialLocation) "Directory was not restored after failure."
    $gmailWebClient.redirect_uris = @("https://unapproved.example/callback")
    [System.IO.File]::WriteAllText($gmailConfigFile, (@{web=$gmailWebClient} | ConvertTo-Json -Depth 5))
    $gmailRejected = $false
    try {
        & $gmailLauncher -ClientConfigPath $gmailConfigFile -Mailbox "inventory@example.test" -OrganizationSlug "example-org" -OutputPath $gmailOutputFile -PythonExecutable "Invoke-GmailTestPython"
    } catch { $gmailRejected = $true }
    Assert-Gmail $gmailRejected "An unregistered callback must fail before authorization."
    Assert-Gmail ($global:gmailTestNativeCalls -eq 2) "An invalid client reached authorization."
    [System.IO.File]::WriteAllText($gmailConfigFile, '{"web":{"client_secret":"synthetic-parser-secret",BROKEN}}')
    $gmailMalformedRejected = $false
    try {
        & $gmailLauncher -ClientConfigPath $gmailConfigFile -Mailbox "inventory@example.test" -OrganizationSlug "example-org" -OutputPath $gmailOutputFile -PythonExecutable "Invoke-GmailTestPython"
    } catch {
        $gmailMalformedRejected = $true
        Assert-Gmail (-not ($_ | Out-String).Contains("synthetic-parser-secret")) "A parse error exposed client JSON."
    }
    Assert-Gmail $gmailMalformedRejected "Malformed client JSON must be rejected."
    Write-Host "Gmail launcher: passed syntax, private settings, cleanup and callback checks."
}
finally {
    [Environment]::SetEnvironmentVariable("VENDOR_MAIL_GMAIL_CLIENT_SECRET", $gmailSavedClientSecret, "Process")
    [Environment]::SetEnvironmentVariable("VENDOR_MAIL_ENABLED", $gmailSavedEnabled, "Process")
    Remove-Item -LiteralPath $gmailTestDirectory -Recurse -Force
    Remove-Item Function:\Invoke-GmailTestPython
}
