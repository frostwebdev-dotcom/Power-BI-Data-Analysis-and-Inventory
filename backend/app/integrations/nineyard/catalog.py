"""Typed, read-only traversal of Nineyard's catalog endpoints.

The HTTP client deliberately exposes only ``GET``.  This module adds the
documented pagination and translates the inconsistent response envelopes into
plain dictionaries for the synchronization service.  It never writes to
Nineyard.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx

from app.integrations.nineyard.errors import NineyardProtocolError

ITEMS_PATH: Final = "/api/Items"
SKUS_PATH: Final = "/api/Skus"
SKU_MAPPINGS_PATH: Final = "/api/Skus/GetSkuMappings"


@dataclass(frozen=True, slots=True)
class SkuProductMapping:
    """One seller SKU and the Nineyard item ids it contains."""

    seller_sku: str
    item_ids: tuple[int, ...]


class ReadOnlyNineyardClient(Protocol):
    def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response: ...


class NineyardCatalogReader:
    """Read all catalog items and the SKU→item relationships."""

    def __init__(
        self,
        client: ReadOnlyNineyardClient,
        *,
        items_per_page: int = 500,
        mapping_batch_size: int = 100,
    ) -> None:
        if items_per_page <= 0 or mapping_batch_size <= 0:
            raise ValueError("page and batch sizes must be positive")
        self._client = client
        self._items_per_page = items_per_page
        self._mapping_batch_size = mapping_batch_size

    def iter_items(self) -> Iterator[dict[str, Any]]:
        """Yield every record from ``GET /api/Items`` exactly once."""
        page = 1
        seen_page_hashes: set[str] = set()
        while True:
            body = self._json(
                ITEMS_PATH,
                params={"Page": page, "PerPage": self._items_per_page},
            )
            if not isinstance(body, Mapping):
                raise NineyardProtocolError(
                    f"{ITEMS_PATH} page {page} returned {type(body).__name__}, expected object"
                )
            records = body.get("itemMapping")
            if not isinstance(records, list):
                raise NineyardProtocolError(f"{ITEMS_PATH} page {page} has no 'itemMapping' array")
            signature = _payload_hash(records)
            if records and signature in seen_page_hashes:
                raise NineyardProtocolError(
                    f"{ITEMS_PATH} repeated page content at page {page}; refusing to loop"
                )
            seen_page_hashes.add(signature)
            for record in records:
                if not isinstance(record, dict):
                    raise NineyardProtocolError(
                        f"{ITEMS_PATH} page {page} contains a non-object record"
                    )
                yield record

            total_pages = body.get("totalPages")
            if isinstance(total_pages, int):
                if page >= total_pages:
                    return
            elif len(records) < self._items_per_page:
                return
            if not records:
                return
            page += 1

    def iter_sku_mappings(self) -> Iterator[SkuProductMapping]:
        """Yield SKU relationships after traversing the bare-array SKU endpoint."""
        account_skus: list[tuple[int, str]] = []
        page = 1
        seen_page_hashes: set[str] = set()
        while True:
            body = self._json(SKUS_PATH, params={"PageNumber": page})
            if not isinstance(body, list):
                raise NineyardProtocolError(
                    f"{SKUS_PATH} page {page} returned {type(body).__name__}, expected array"
                )
            signature = _payload_hash(body)
            if body and signature in seen_page_hashes:
                raise NineyardProtocolError(
                    f"{SKUS_PATH} repeated page content at page {page}; refusing to loop"
                )
            seen_page_hashes.add(signature)
            for record in body:
                if not isinstance(record, Mapping):
                    raise NineyardProtocolError(
                        f"{SKUS_PATH} page {page} contains a non-object record"
                    )
                account_sku_id = record.get("accountSkuId")
                sku = record.get("sku")
                if isinstance(account_sku_id, int) and isinstance(sku, str) and sku.strip():
                    account_skus.append((account_sku_id, sku.strip()))
            if not body:
                break
            page += 1

        sku_by_id = dict(account_skus)
        ids = list(sku_by_id)
        for start in range(0, len(ids), self._mapping_batch_size):
            batch = ids[start : start + self._mapping_batch_size]
            body = self._json(SKU_MAPPINGS_PATH, params={"AccountSkuIds": batch})
            if not isinstance(body, list):
                raise NineyardProtocolError(
                    f"{SKU_MAPPINGS_PATH} returned {type(body).__name__}, expected array"
                )
            for record in body:
                if not isinstance(record, Mapping):
                    raise NineyardProtocolError(f"{SKU_MAPPINGS_PATH} contains a non-object record")
                account_sku_id = record.get("accountSkuId")
                mapped = record.get("mappedItems")
                if not isinstance(account_sku_id, int) or account_sku_id not in sku_by_id:
                    continue
                if not isinstance(mapped, Sequence) or isinstance(mapped, str | bytes):
                    mapped = []
                item_ids = tuple(
                    item_id
                    for item in mapped
                    if isinstance(item, Mapping)
                    and isinstance((item_id := item.get("itemId")), int)
                )
                yield SkuProductMapping(sku_by_id[account_sku_id], item_ids)

    def _json(self, path: str, *, params: dict[str, Any]) -> Any:
        response = self._client.get(path, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise NineyardProtocolError(f"{path} did not return JSON") from exc


def _payload_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
