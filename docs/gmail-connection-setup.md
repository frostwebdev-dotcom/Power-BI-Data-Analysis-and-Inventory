# Connect the inventory Gmail mailbox

The Gmail importer is deployed. This one-time setup obtains the separate Google
grant needed for that importer. It does not import stock or turn on polling.
Use a client-owned Google Cloud project where available. An operator-owned
project can be used with the client's agreement and a plan for ownership/access
at handover. The receiving mailbox's owner separately approves Gmail access.

Validated in [CI #130](https://github.com/frostwebdev-dotcom/Power-BI-Data-Analysis-and-Inventory/actions/runs/42557320094):
1,426 backend tests and 21 browser tests passed, including 20 authorization tests
with simulated Google responses and a real local callback listener. Windows
PowerShell 5.1 tests verified launcher validation, private environment handling
and restoration after success/failure. These tests do not constitute real
mailbox consent. PR #20 merged as `2f009d0b5d95e2442707ced7a37da39e4bc261fc`.

## 1. Prepare the Google web client

In Google Cloud Console:

1. Select or create the client's project and enable the **Gmail API**.
2. Configure **Google Auth platform** branding/audience for the inventory app.
   Use the real client/project contact details; do not invent them.
3. Request only `https://www.googleapis.com/auth/gmail.readonly`. This grant can
   read that mailbox; vendor routing/labels are application filters, not
   narrower Google permission boundaries. The importer never sends or edits mail.
4. Create an OAuth client of type **Web application** named **PRMS vendor
   inventory setup**, with this exact authorized redirect URI:

   ```text
   http://127.0.0.1:8765/oauth/callback
   ```

   No JavaScript origin or public callback endpoint is needed for this local
   setup. Google permits localhost IP redirects for the web-server flow.
5. Download the client's JSON privately onto the operator's computer, outside
   the repository. It contains a client secret; do not send it in chat, attach it
   to a ticket, commit it or upload it to shared artifact storage.
6. Confirm the app's publishing/verification requirements. External **Testing**
   refresh tokens with Gmail scopes expire in seven days. A successful setup is
   not proof of a durable unattended grant; complete the applicable Google
   publishing/verification process before promising daily operation.

### Public Google Branding information

Google can disable **Publish app** until Branding has a valid name, monitored
support email, public homepage and public privacy-policy URL. The authenticated
dashboard alone does not meet Google's public homepage requirements.

The public-information release adds `/app-info.html` and `/privacy.html` on the
existing frontend. Both are static HTML documents, readable without sign-in or
JavaScript, and contain no private inventory or mailbox records. They are also
linked from sign-in and automatic imports. After deploying and checking that
release, use these paths as the homepage and privacy-policy URLs respectively.
Do not enter a fabricated terms URL; no terms document is supplied by this change.

Successful hosting does not establish authorized-domain ownership or Google
verification. Complete Google's applicable domain and verification steps. Do
not claim ownership of Microsoft's shared hosting domain. If Google requires a
domain you control, configure a real owned domain before completing that step.

After moving the project out of Testing, repeat the local authorization flow to
obtain a grant issued under the new publishing state. Replace the private Azure
refresh-token secret with that grant's value, then restart the referencing API
revision or deploy a new one. Keep polling false until vendor acceptance passes.

The mailbox owner must review and approve Google's read-only consent screen.
Existing Microsoft/Entra sign-in and a connected ChatGPT Gmail account do not
provide this product's refresh token.

## 2. Authorize on the operator's own computer

The browser and local callback must run on the **same computer**. Do not run this
command in the Azure container console or point the ChatGPT cloud browser at its
loopback URL. Keep the private Google JSON and generated runtime file on that
computer. This helper does not ask for a Gmail password.

On Windows, use Python 3.12 or later and a current checkout of this repository.
From the repository folder in PowerShell, prepare its Python environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\backend
```

Run the launcher, replacing only the paths, actual receiving address and existing
organization slug. These example addresses are placeholders:

```powershell
.\infra\scripts\connect-gmail.ps1 `
  -ClientConfigPath "$env:USERPROFILE\Downloads\google-web-client.json" `
  -Mailbox "inventory@example.com" `
  -OrganizationSlug "your-existing-organization-slug" `
  -PythonExecutable (Resolve-Path .\.venv\Scripts\python.exe).Path
```

Google opens in the local system browser. The owner selects the receiving
mailbox and reviews the consent request. The helper validates state and PKCE,
exchanges the code, verifies the exact profile email/scope, then verifies a fresh
token exchange using the same reader the unattended importer uses. Only the
profile is checked; no messages are imported or read during setup.

After verification, the launcher reports the path of a new private runtime JSON
under `%LOCALAPPDATA%\PRMS\GmailSetup`. It contains environment-variable names
and values, including the refresh token. Windows file permissions are restricted
before credentials are written. Existing files are never overwritten; failed
or cancelled setup removes only its new incomplete output. The launcher restores
the shell's previous environment and directory, including on failure.

For other operating systems or an existing private environment, use the CLI
directly after setting `VENDOR_MAIL_PROVIDER=gmail`, the Gmail client ID/secret,
mailbox and organization slug privately in environment variables:

```text
python -m app.cli.vendor_mail_authorize --output /private/prms/gmail-runtime.json
```

The destination must be outside Git and in an owner-only directory. The CLI does
not print tokens or the authorization response. Its callback listens only on
IPv4 loopback, rejects wrong/replayed state and hosts, and stops after a bounded
wait. If port 8765 is unavailable, free that local port or explicitly register
the exact alternate URI before using `--port`.

## 3. Configure and accept the unattended connection

The runtime JSON is a private **environment mapping**, not a vendor stock file.
Do not upload it on Imports, attach it in chat or commit it. A runtime administrator
copies its values into the backend's private Azure configuration, using secrets
for the client secret and refresh token. The helper does not change Azure
settings automatically. `VENDOR_MAIL_ENABLED` in this mapping is **false**.

Verify the organization, mailbox, Gmail label and app publishing status. Obtain
each vendor's original sender headers and approved file/link rules. Check an
attachment and a verified direct download through the existing import reports,
including profile versions, raw hashes, duplicate replay and failed-file review,
before enabling normal polling. No missing email clears previous stock.

## Latest owner-reported setup (9 November 2026)

The owner created the Google OAuth configuration and ran the helper on the
receiving mailbox owner's Windows computer. The displayed terminal result
reported a verified mailbox and privately saved runtime settings. The owner
configured Azure secrets and the API revision `gmail-setup-1`, which was shown
running with all traffic. The app now displays Gmail configured with automatic
checks paused, no sources, and no successful mailbox check yet.

The Google project still shows Testing. Publishing is blocked by missing public
Branding URLs. The public-information pages are being prepared for deployment;
their existence alone does not prove Google publishing, domain verification or
live email-import acceptance. No credentials have been supplied to this chat.

Earlier cloud-browser attempts to open Google Cloud Console returned **Site
Unavailable**; the owner successfully used their own browser instead. That
session limitation is not evidence of a Google outage.

References:
- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/workspace/guides/create-credentials
- https://developers.google.com/identity/protocols/oauth2/#expiration
