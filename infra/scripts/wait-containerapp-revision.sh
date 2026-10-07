#!/usr/bin/env bash
# Require the named revision, rather than accepting an older healthy revision.
set -euo pipefail
resource_group=${1:?resource group required}
app_name=${2:?app name required}
revision=${3:?revision required}
for attempt in $(seq 1 60); do
  health=$(az containerapp revision show --resource-group "$resource_group" \
    --name "$app_name" --revision "$revision" \
    --query properties.healthState --output tsv) || health=Unknown
  ready=$(az containerapp show --resource-group "$resource_group" --name "$app_name" \
    --query properties.latestReadyRevisionName --output tsv) || ready=Unknown
  echo "Attempt $attempt: revision=$revision; health=$health; ready=$ready"
  if [ "$health" = Healthy ] && [ "$ready" = "$revision" ]; then exit 0; fi
  sleep 10
done
echo "The target revision did not become ready within 10 minutes." >&2
exit 1
