# Enable Microsoft sign-in in Azure staging

The code change adds the missing sign-in implementation. Azure resource health
alone does not enable login. Complete these steps after the pull request checks
pass. This is an application identity setup, separate from the GitHub deployment
managed identity. Never reuse the GitHub identity as a browser app registration.

## 1. Register the API

In the correct directory (tenant `17bbcdfa-a0c5-4dfe-a851-0c7459906680`), open
Microsoft Entra ID → App registrations → New registration.

- Name: `prms-api-staging-auth`.
- Supported account types: accounts in this organizational directory only.
- Redirect URI: leave empty for the API.
- Copy the **Application (client) ID**. This becomes `ENTRA_API_CLIENT_ID`.
- Open **Expose an API**. Set Application ID URI to `api://<API_CLIENT_ID>`.
- Add scope `access_as_user`, enabled, with admin-only consent. Suggested display
  name: `Access PRMS as the signed-in user`; description: `Access PRMS on behalf
  of an approved user`.
- In **Manifest**, set the API's requested access-token version to 2. In the
  Microsoft Graph manifest this is `api.requestedAccessTokenVersion: 2`.
  Older manifest views name it `accessTokenAcceptedVersion`. Preserve all other
  existing properties. A v1 access token will be deliberately rejected.

## 2. Register the browser app

Create another registration in the same directory:

- Name: `prms-web-staging-auth`.
- Supported account types: this organizational directory only.
- Authentication platform: **Single-page application (SPA)**.
- Exact redirect URI:
  `https://ca-prms-web-staging.thankfulground-2dc62bbd.eastus2.azurecontainerapps.io/auth/callback.html`.
- Copy its **Application (client) ID**. This becomes `ENTRA_SPA_CLIENT_ID`.
- Under **API permissions**, add **My APIs → prms-api-staging-auth → Delegated
  permissions → access_as_user**. Grant admin consent in the directory.
- No client secret, certificate, implicit-flow checkbox, or public password grant
  is required for this browser app.

Keep the API and SPA registrations separate. The API verifies the audience against
its own client ID and the `azp` claim against the browser's client ID.

## 3. Select the exact approved user

Under Microsoft Entra ID → Users, choose the person allowed to access PRMS.
Copy that user's **Object ID in this directory**. This is not an application ID,
managed-identity principal ID, or a user ID from a different directory.

For a guest, invite the person into this directory first and use their guest
user Object ID here. Do not use their email address as an authentication key.
Select their PRMS role explicitly. ADMIN is full PRMS access; it does not grant
Azure or directory administration permissions. Enrollment never happens on login.

## 4. Obtain the reviewed code and publish both images

Merge the PR only after backend, frontend, and compose-smoke checks pass, then
pull `main` in your Windows repository. Publish a new backend and frontend image
with unique tags. The frontend needs only the existing API URL build argument;
Entra IDs are loaded from the API at runtime. Keep the existing registry identities,
secret references, ingress target ports (API 8000, web 3000), and CORS configuration.

For a manual release from your repository root, use a new tag, for example:

```powershell
$releaseTag = "staging-entra-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$acrLoginServer = az acr show --name acrprmsstaginggs --query loginServer -o tsv
az acr login --name acrprmsstaginggs

docker build --platform linux/amd64 -t "$acrLoginServer/prms-api:$releaseTag" -f .\backend\Dockerfile .\backend
if ($LASTEXITCODE -ne 0) { throw "Backend build failed" }
docker push "$acrLoginServer/prms-api:$releaseTag"
if ($LASTEXITCODE -ne 0) { throw "Backend push failed" }

docker build --platform linux/amd64 --build-arg "NEXT_PUBLIC_API_BASE_URL=https://ca-prms-api-staging.thankfulground-2dc62bbd.eastus2.azurecontainerapps.io" -t "$acrLoginServer/prms-web:$releaseTag" -f .\frontend\Dockerfile .\frontend
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
docker push "$acrLoginServer/prms-web:$releaseTag"
if ($LASTEXITCODE -ne 0) { throw "Frontend push failed" }
```

If local image uploads time out, use the previously configured GitHub backend
pipeline or an ACR build from a clean frontend context. Do not upload generated
`node_modules` or `.next` source archives. Both final image tags must exist before
updating either application. The existing CI deployment job deploys the backend
only; this change does not silently add frontend deployment permissions.

## 5. Deploy the backend and apply the additive migration

Record the current image for rollback before deploying. Keep `AUTH_BACKEND=dev`
and `DEV_AUTH_ENABLED=false` for this first rollout. Deploying the image alone
keeps staging sign-in disabled until configuration and enrollment are complete.

```powershell
az containerapp show --resource-group rg-prms-staging --name ca-prms-api-staging --query "properties.template.containers[].{Name:name,Image:image}" -o json
az containerapp update --resource-group rg-prms-staging --name ca-prms-api-staging --container-name prms-api --image "$acrLoginServer/prms-api:$releaseTag" --output none
if ($LASTEXITCODE -ne 0) { throw "Backend deploy failed" }
```

Wait until the new revision is Healthy, then connect to it:

```powershell
az containerapp exec --resource-group rg-prms-staging --name ca-prms-api-staging --container prms-api --command "/bin/sh"
```

Inside the Linux container shell:

```sh
alembic upgrade head
alembic current
alembic check
```

Expected head: `e71a20261007`. Stop on an error. Do not stamp the migration or
replace the database. Existing catalogs, orders, and users are retained. The new
nullable identity columns are compatible with the previous image for rollback.

## 6. Configure the API to use Entra

Exit the container shell (`exit`), then use PowerShell. Replace the two placeholders
with the actual application IDs from steps 1 and 2:

```powershell
$apiClientId = "<API_APPLICATION_CLIENT_ID>"
$spaClientId = "<SPA_APPLICATION_CLIENT_ID>"
az containerapp update --resource-group rg-prms-staging --name ca-prms-api-staging --container-name prms-api --set-env-vars "AUTH_BACKEND=entra" "DEV_AUTH_ENABLED=false" "ENTRA_TENANT_ID=17bbcdfa-a0c5-4dfe-a851-0c7459906680" "ENTRA_API_CLIENT_ID=$apiClientId" "ENTRA_SPA_CLIENT_ID=$spaClientId" "ENTRA_REQUIRED_SCOPE=access_as_user" --output none
if ($LASTEXITCODE -ne 0) { throw "Authentication configuration failed" }
```

These IDs and the scope are public configuration, not Key Vault secrets. The
existing database and integration secret references are left in place. No Microsoft
password belongs in Key Vault or in `NEXT_PUBLIC_*`. Wait for the new revision to
be Healthy before reconnecting.

## 7. Explicitly enroll the approved account

Reconnect with the exec command from step 5. Inside the container, run the following
as one line after replacing every placeholder. ADMIN is shown only for the explicitly
approved initial administrator; use VIEWER/PURCHASING_MANAGER/DATA_OPERATOR for others.

```sh
python -m app.cli.enroll_entra_user --org-slug global-supplies --tenant-id 17bbcdfa-a0c5-4dfe-a851-0c7459906680 --object-id <USER_OBJECT_ID_IN_THIS_DIRECTORY> --email <APPROVED_USER_EMAIL> --display-name "<APPROVED_USER_NAME>" --role ADMIN --operator-label "<PERSON_APPROVING_THIS_GRANT>"
```

Expected output: `Entra account enrolled: <internal UUID>; role: ADMIN`.
The command is idempotent, audits enrollment and the requested role grant, and
refuses to overwrite a different identity or reactivate an inactive user. It
requires privileged database access; do not expose it as a public HTTP endpoint.

## 8. Deploy the frontend and verify

Exit to PowerShell:

```powershell
az containerapp update --resource-group rg-prms-staging --name ca-prms-web-staging --container-name prms-web --image "$acrLoginServer/prms-web:$releaseTag" --output none
if ($LASTEXITCODE -ne 0) { throw "Frontend deploy failed" }
curl.exe --fail --max-time 30 "https://ca-prms-api-staging.thankfulground-2dc62bbd.eastus2.azurecontainerapps.io/api/v1/auth/config"
curl.exe --fail --max-time 30 "https://ca-prms-api-staging.thankfulground-2dc62bbd.eastus2.azurecontainerapps.io/api/v1/health"
```

Public config must say `mode: entra`, with the correct browser client ID and
`api://<API_CLIENT_ID>/access_as_user` scope. The database health endpoint must
return 200. Open the web app, hard-refresh, click **Sign in with Microsoft**, and
use the enrolled account. Allow the sign-in popup. Verify the account and role
shown by `/api/v1/auth/me` in browser Network tools; never paste bearer tokens into
chat. Test sign-out and another unenrolled account to confirm it cannot enter.

If Azure Container Apps built-in Authentication is separately enabled, inspect
that gateway configuration before debugging a bearer-token failure. This runbook
uses the application's own Entra validator; the runtime managed identity grants
image/Key Vault access and does not sign users into PRMS.

## Troubleshooting and rollback

- Microsoft redirect mismatch: exact SPA redirect must end `/auth/callback.html`.
- Consent/admin approval error: finish consent for the API delegated scope.
- Account has not been granted access: enroll the matching directory Object ID.
- Invalid access token: API token version 2, correct API audience, browser client,
  tenant and delegated scope are all required. A Graph access token or ID token
  cannot call this API.
- Still shows email-only login: deploy the updated frontend and hard-refresh;
  inspect `/auth/config`. Do not enable development authentication to bypass it.
- API configuration error: all three Entra IDs must be valid UUIDs; API and SPA IDs
  must differ.
- API health remains green but protected requests fail: health is intentionally
  unauthenticated, so it does not prove login works.

For rollback, restore the recorded previous API/frontend images and remove the
new AUTH_BACKEND/ENTRA_* settings (or set AUTH_BACKEND=dev with DEV_AUTH_ENABLED=false).
Keep the additive identity migration in place. The old image ignores those columns.
Staging will return to disabled sign-in; rollback must not enable passwordless login.

## Verification and references

Source tests use locally signed RSA fixtures to verify token boundaries and
PostgreSQL tests verify enrollment/revocation. Browser UI tests mock API responses;
they do not claim a live Microsoft sign-in. Live verification requires the tenant,
consent, the approved account, and the deployed pair of images above.

- [Microsoft token claim validation](https://learn.microsoft.com/en-us/entra/identity-platform/claims-validation)
- [MSAL browser initialization](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/initialization)
- [MSAL access-token acquisition](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/acquire-token)
- [Expose an API](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-expose-web-apis)
