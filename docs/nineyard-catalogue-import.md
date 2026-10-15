# Import a Nineyard Products catalogue export

This is master catalogue enrichment, not a vendor inventory import. Keep the
original CSV unchanged. Do not use the PCA supplier profile for it. Required
headers are `ItemId`, `Product ID`, `Title` and `Upc`; their positions may vary.

The importer uses A `ItemId` to find the existing product, F `Product ID` as its
business catalogue number, and U `Upc` as one or more comma-separated barcodes.
UUIDs, approved mappings, stock quantities and existing lifecycle states stay
intact. New CSV-only products are inactive until a subsequent API sync confirms
their state. Missing export rows do not deactivate products. Shared valid
barcodes are retained for every product and produce ambiguous matching results.

## Deploy the code and migration

Deploy this backend version and run `alembic upgrade head` in the API container.
This migration adds an external ID and backfills it from retained API source
records. Do not run the old backend sync after importing corrected catalogue
numbers. Temporarily pause scheduled Nineyard synchronization during migration
and deployment. Restore its previous setting once the new version is healthy.

## Validate the file without a database

From `backend/`, with the application installed:

```sh
python -m app.cli.nineyard_catalogue_import --file /path/to/export.csv --file-only
```

It prints the SHA-256, barcode diagnostics and structural errors. Invalid
barcodes are skipped, not repaired by guessing digits. Scientific notation
must be corrected from an authoritative source. Short UPC-E values without
a supplied check digit are not accepted. Eleven-digit UPC-A values use the
existing application's leading-zero restoration and checksum verification.

## Dry run against staging

Put the original CSV in the existing persistent `/storage/raw` mount, then run:

```sh
python -m app.cli.nineyard_catalogue_import \
  --file /storage/raw/nineyard-products.csv --org-slug global-supplies
```

This reads the database without changing products, identifiers, audits or stored
files. Review the `file` and `database` counts. `blocking_errors` must be zero.
The JSON includes the first 20 warnings/errors and full aggregate counts.
Conflicting identities block the entire apply. Missing external provenance
needs investigation; never patch it with a guessed catalogue association.

## Apply the reviewed exact bytes

Use the SHA-256 printed by the dry run, not a hash from a different file:

```sh
python -m app.cli.nineyard_catalogue_import \
  --file /storage/raw/nineyard-products.csv --org-slug global-supplies \
  --apply --expected-sha256 REVIEWED_SHA256 --operator-label "Osama Khan"
```

Apply rebuilds the plan while holding the same tenant lock used by API sync.
All database changes and audits commit together or roll back together. Raw CSV
bytes are retained under a content-addressed storage URI and rows retain their
original field values. Reapplying identical input is idempotent. No approved
vendor or Amazon mapping is recomputed, superseded or deleted by this command.

## Verify

Search Products by a known business catalogue number and barcode. Check that
the old product UUID and approved mapping links are unchanged. Run a Nineyard
sync and repeat the checks to confirm the export is preserved. Rerun the dry
run: already imported barcodes should be unchanged. Confirm a shared barcode
produces multiple active candidates and requires review.

This command does not rematch completed supplier imports or rewrite historical
snapshots. Use a subsequent authorized inventory import to exercise the enriched
catalogue. An identical supplier file is deduplicated by the existing upload
workflow, so simply re-uploading the same bytes is not a rematch.
