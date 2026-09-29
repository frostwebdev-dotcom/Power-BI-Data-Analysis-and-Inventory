"""Nineyard catalog traversal against scripted, in-memory responses."""

from __future__ import annotations

from typing import Any

import httpx2 as httpx
import pytest

from app.integrations.nineyard.catalog import (
    DEFAULT_ITEMS_PER_PAGE,
    DEFAULT_SKU_REQUEST_INTERVAL_SECONDS,
    NineyardCatalogReader,
    SkuProductMapping,
)
from app.integrations.nineyard.errors import NineyardNotFoundError, NineyardProtocolError


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append((path, params or {}))
        if not self.responses:
            raise AssertionError("unexpected request")
        request = httpx.Request("GET", f"https://nineyard.test{path}")
        return httpx.Response(200, json=self.responses.pop(0), request=request)


def test_default_item_page_size_matches_the_live_api_limit() -> None:
    assert DEFAULT_ITEMS_PER_PAGE == 200
    assert DEFAULT_SKU_REQUEST_INTERVAL_SECONDS == 1.25


def test_an_account_filter_is_required() -> None:
    with pytest.raises(ValueError, match="tenant-wide"):
        NineyardCatalogReader(FakeClient([]))


def test_items_follow_documented_pages() -> None:
    client = FakeClient(
        [
            {"totalPages": 2, "itemMapping": [{"itemId": 1}]},
            {"totalPages": 2, "itemMapping": [{"itemId": 2}]},
        ]
    )

    rows = list(
        NineyardCatalogReader(client, sku_account="Seller Account", items_per_page=1).iter_items()
    )

    assert rows == [{"itemId": 1}, {"itemId": 2}]
    assert client.calls == [
        ("/api/Items", {"Page": 1, "PerPage": 1}),
        ("/api/Items", {"Page": 2, "PerPage": 1}),
    ]


def test_sku_pages_are_joined_to_item_mappings_in_batches() -> None:
    client = FakeClient(
        [
            [{"accountSkuId": 11, "sku": " SKU-A "}],
            [{"accountSkuId": 12, "sku": "SKU-B"}],
            [
                {"accountSkuId": 11, "mappedItems": [{"itemId": 101, "qty": 1}]},
                {
                    "accountSkuId": 12,
                    "mappedItems": [{"itemId": 102, "qty": 1}, {"itemId": 103, "qty": 2}],
                },
            ],
        ]
    )

    rows = list(
        NineyardCatalogReader(
            client,
            sku_account="Seller Account",
            seller_skus=["SKU-A", "SKU-B"],
            mapping_batch_size=10,
            sku_request_interval_seconds=0,
        ).iter_sku_mappings()
    )

    assert [(row.seller_sku, row.item_ids) for row in rows] == [
        ("SKU-A", (101,)),
        ("SKU-B", (102, 103)),
    ]
    assert client.calls == [
        (
            "/api/Skus",
            {"PageNumber": 1, "Account": "Seller Account", "Sku": "SKU-A"},
        ),
        (
            "/api/Skus",
            {"PageNumber": 1, "Account": "Seller Account", "Sku": "SKU-B"},
        ),
        ("/api/Skus/GetSkuMappings", {"AccountSkuIds": [11, 12]}),
    ]


def test_exact_sku_requests_are_proactively_paced(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []
    monkeypatch.setattr("app.integrations.nineyard.catalog.time.sleep", delays.append)
    client = FakeClient(
        [
            [{"accountSkuId": 11, "sku": "SKU-A"}],
            [{"accountSkuId": 11, "mappedItems": [{"itemId": 101}]}],
        ]
    )

    rows = list(
        NineyardCatalogReader(
            client,
            sku_account="Seller Account",
            seller_skus=["SKU-A"],
            sku_request_interval_seconds=1.25,
        ).iter_sku_mappings()
    )

    assert rows == [SkuProductMapping("SKU-A", (101,))]
    assert delays == [1.25]


def test_a_stale_mapping_id_does_not_discard_valid_ids() -> None:
    class StaleMappingClient:
        def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
            request = httpx.Request("GET", f"https://nineyard.test{path}")
            if path == "/api/Skus":
                sku = str((params or {})["Sku"])
                account_sku_id = 11 if sku == "SKU-A" else 12
                return httpx.Response(
                    200,
                    json=[{"accountSkuId": account_sku_id, "sku": sku}],
                    request=request,
                )
            ids = list((params or {})["AccountSkuIds"])
            if 12 in ids:
                raise NineyardNotFoundError("stale account SKU", status_code=404)
            return httpx.Response(
                200,
                json=[{"accountSkuId": 11, "mappedItems": [{"itemId": 101}]}],
                request=request,
            )

    reader = NineyardCatalogReader(
        StaleMappingClient(),
        sku_account="Seller Account",
        seller_skus=["SKU-A", "SKU-B"],
        sku_request_interval_seconds=0,
    )

    assert list(reader.iter_sku_mappings()) == [SkuProductMapping("SKU-A", (101,))]


def test_repeated_page_is_rejected() -> None:
    client = FakeClient(
        [
            {"totalPages": 3, "itemMapping": [{"itemId": 1}]},
            {"totalPages": 3, "itemMapping": [{"itemId": 1}]},
        ]
    )

    with pytest.raises(NineyardProtocolError, match="repeated page"):
        list(
            NineyardCatalogReader(
                client,
                sku_account="Seller Account",
                items_per_page=1,
            ).iter_items()
        )


def test_wrong_envelope_is_a_protocol_error() -> None:
    client = FakeClient([[{"itemId": 1}]])

    with pytest.raises(NineyardProtocolError, match="expected object"):
        list(NineyardCatalogReader(client, sku_account="Seller Account").iter_items())
