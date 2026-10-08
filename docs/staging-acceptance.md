# Complete the Azure staging acceptance

The catalogue, product picker and Amazon review changes are code deliverables.
The following live checks establish hosted acceptance. Existing local Docker
acceptance remains recorded in [milestone-1-handoff.md](milestone-1-handoff.md).

## 1. Enable full-stack GitHub deployment

The `CI` workflow now publishes both images from the tested commit, deploys the
API before the web app, waits for each exact revision and checks HTTP, database,
Microsoft sign-in configuration and CORS. It runs only through **Run workflow →
main → deploy_staging**, after all three test jobs pass. Push/PR runs only test.
Application configuration, registry identities, secrets and mounts are preserved.
The API is kept at one replica because it owns the in-process scheduler.

The existing GitHub identity can write `prms-api`. Add its permissions for
`prms-web` and the web Container App once, using an account allowed to assign
Azure roles. These commands do not grant directory administration or vault access.
Run in PowerShell from the repository root:

```powershell
.\infra\scripts\enable-staging-web-deployment.ps1
```

The script updates the existing Writer assignment to allow exactly `prms-api`
and `prms-web`, then grants Contributor on the web app. It preserves API image
access and avoids attempting a duplicate registry assignment.
Keep the `staging` environment OIDC credential and its existing subject. No new
client secret is needed. See Microsoft's [repository ABAC permissions](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-rbac-abac-repository-permissions).
Allow role propagation before running the deployment.

After review and merging, pull `main`, then run the manual deployment. Save the
run URL and both revision names. This change has no new database migration;
`alembic current` should remain `e71a20261007 (head)` and `alembic check` should
report no new upgrade operations. If a later release adds migrations, follow
the migration/backup procedure before promoting it.

The workflow records prior revisions/images. A failed deployment does not
automatically roll back or certify acceptance; inspect logs and restore the
recorded image if needed. The two app updates are sequential, not atomic.

## 2. Persist retained uploads before the vendor walkthrough

Container-local raw files disappear when their replica is replaced. Provision
the classic Azure Files share and its environment storage definition using the
included Bicep template. It creates a Standard LRS storage account with a
100 GiB share quota; storage is billed by Azure. The template does not deploy
another API, overwrite its secrets or output a storage account key.

First confirm the new backend is deployed: its `appuser` has fixed UID/GID
`10001`, which the SMB mount uses. Wait for active imports/syncs to finish. If
raw files already exist in an ephemeral replica, preserve their complete
organization/year/month directory tree before mounting the share; attaching a
share hides the old directory and this script deliberately does not copy files.
If the API already has a persistent mount at `/storage` or `/storage/raw`, keep
it and skip this setup, then verify it below.

```powershell
az deployment group create --subscription 'e3a5c050-b241-4488-83ec-6473da22f05a' --resource-group rg-prms-staging --name prms-raw-storage --template-file .\infra\azure\staging-raw-storage.bicep --parameters location=eastus2 --query properties.outputs --output json
az containerapp exec --resource-group rg-prms-staging --name ca-prms-api-staging --container prms-api --command 'id'
```

Only after the displayed UID/GID are `10001`, attach the new environment
storage. The script reads the existing template, adds the named mount, retains
other mounts and settings, and creates a revision. It refuses to replace an
existing raw storage mount.

The script uses the signed-in Azure CLI account, keeps its access token in
memory, and follows Azure's asynchronous operation URL. An HTTP `202 Accepted`
is not success: the script waits for the operation and for a new ready revision
containing the mount. If it fails or times out, retain its operation URL and
correlation ID and inspect that operation before retrying; do not submit
overlapping updates. The script's successful output verifies configuration and
readiness, while file writes and persistence still need the checks below.

```powershell
.\infra\scripts\attach-staging-raw-storage.ps1
```

Wait for that new revision to become Healthy/Running. Inspect the read-only
configuration (no secret values are requested):

```powershell
az containerapp show --resource-group rg-prms-staging --name ca-prms-api-staging --query '{Revision:properties.latestRevisionName,Scale:properties.template.scale,Volumes:properties.template.volumes,Mounts:properties.template.containers[].volumeMounts}' --output json
```

Require an `AzureFile` volume linked to `prms-raw`, mounted in `prms-api` at
`/storage/raw`. An `EmptyDir` volume does not prove persistence. See Microsoft's
[storage mount guide](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts).

## 3. Verify both enrolled admins and the new screens

Open [the staging app](https://ca-prms-web-staging.thankfulground-2dc62bbd.eastus2.azurecontainerapps.io).
Use separate browser sessions for both enrolled administrator accounts.
Each must sign in through Microsoft and load the
same organization. Enrollment success alone is not a browser login test.

1. In **Products**, search a known Nineyard catalogue number, then its name and
   UPC. Open **Details**; check identifiers, provenance and multiple mapped SKUs.
   Confirm inactive products appear only when requested.
2. In **Exception queue**, open an Amazon exception. Check seller SKU, ASIN,
   title, mapping status and rule trail. Search for a product, inspect its
   details, explicitly **Select**, then approve only a verified relationship.
   Search results never approve automatically. Leave uncertain mappings pending.
3. Confirm the approval in the audit log. On the next listings sync, the
   approved mapping should remain. Use **Watch this product** to confirm the
   picker receives the selected catalogue product.

Open exceptions reflect records needing review; clearing the count is not an
acceptance requirement. Viewer accounts may read the catalogue and source
context but must not receive approval/watch write controls.

## 4. Prove CSV and XLSX processing in staging

Use two representative vendor files (one CSV, one XLSX). For each vendor:

1. Create the vendor and its versioned import profile in the UI. Validate the
   columns against a sample, preserving barcode text/leading zeroes.
2. Upload the file. Check final status, row counts, validation errors and rule
   outcomes in the report. Confirm descriptions produce suggestions requiring
   approval, rather than automatic mappings.
3. Download **raw file** and compare SHA-256 with the original using
   `Get-FileHash -Algorithm SHA256` on both files.
4. Approve one independently verified uncertain SKU, then upload a later file
   with changed bytes and that same SKU. Confirm the stored approved mapping is
   reused; identical bytes correctly return the duplicate import instead.
5. Watch the selected product. Import an out-of-stock snapshot followed by an
   available snapshot and confirm the watch status, availability event and audit.
6. After jobs finish, restart the API revision (or deploy another revision).
   Download the same raw file again and verify the same checksum. This proves
   retention beyond one replica, not just a successful initial upload.

Record vendor codes, profile versions, import IDs, hashes, decision IDs and
availability event IDs, without recording credentials or raw commercial files
in Git. The automated walkthrough uses fixtures; it does not replace this
client-data acceptance.

## 5. Verify scheduled ingestion and retention evidence

From the API console, run:

```sh
python -m app.cli.staging_check --org-slug global-supplies
```

This command performs reads only. It prints product/listing counts, non-secret
scheduler settings, the latest **SCHEDULED** run for each integration job and
checksum verification of up to ten latest retained files. Missing scheduled
rows or zero checked files mean evidence is still pending; exit code zero
means the check ran without a file integrity failure, not that acceptance passed.

Require `AUTH_BACKEND=entra`, development authentication disabled, both
integration schedulers enabled, and the organization slugs bound to
`global-supplies` (or an unambiguous single active organization). Keep the API
at **min=1, max=1**, a single active revision and one Uvicorn process while it
owns the in-process scheduler. The web app may scale independently.

After each configured interval, require fresh completed scheduled runs. Manual
CLI syncs do not prove scheduling. The default Amazon inventory interval is
60 minutes; orders/listings and Nineyard are 1,440 minutes. Restarting the API
re-registers jobs and starts their intervals again. Check console logs for
`jobs.scheduler_started` / `jobs.scheduled` and failed jobs if no scheduled run
appears. Do not launch overlapping manual retries during this verification.

Hosted acceptance is complete only after these live results are recorded.
