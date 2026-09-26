"""Nineyard catalog traversal against scripted, in-memory responses."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.integrations.nineyard.catalog import NineyardCatalogReader
from app.integrations.nineyard.errors import NineyardProtocolError


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


def test_items_follow_documented_pages() -> None:
    client = FakeClient(
        [
            {"totalPages": 2, "itemMapping": [{"itemId": 1}]},
            {"totalPages": 2, "itemMapping": [{"itemId": 2}]},
        ]
    )

    rows = list(NineyardCatalogReader(client, items_per_page=1).iter_items())

    assert rows == [{"itemId": 1}, {"itemId": 2}]
    assert client.calls == [
        ("/api/Items", {"Page": 1, "PerPage": 1}),
        ("/api/Items", {"Page": 2, "PerPage": 1}),
    ]


def test_sku_pages_are_joined_to_item_mappings_in_batches() -> None:
    client = FakeClient(
        [
            [{"accountSkuId": 11, "sku": " SKU-A "}, {"accountSkuId": 12, "sku": "SKU-B"}],
            [],
            [
                {"accountSkuId": 11, "mappedItems": [{"itemId": 101, "qty": 1}]},
                {
                    "accountSkuId": 12,
                    "mappedItems": [{"itemId": 102, "qty": 1}, {"itemId": 103, "qty": 2}],
                },
            ],
        ]
    )

    rows = list(NineyardCatalogReader(client, mapping_batch_size=10).iter_sku_mappings())

    assert [(row.seller_sku, row.item_ids) for row in rows] == [
        ("SKU-A", (101,)),
        ("SKU-B", (102, 103)),
    ]
    assert client.calls[-1] == ("/api/Skus/GetSkuMappings", {"AccountSkuIds": [11, 12]})


def test_repeated_page_is_rejected() -> None:
    client = FakeClient(
        [
            {"totalPages": 3, "itemMapping": [{"itemId": 1}]},
            {"totalPages": 3, "itemMapping": [{"itemId": 1}]},
        ]
    )

    with pytest.raises(NineyardProtocolError, match="repeated page"):
        list(NineyardCatalogReader(client, items_per_page=1).iter_items())


def test_wrong_envelope_is_a_protocol_error() -> None:
    client = FakeClient([[{"itemId": 1}]])

    with pytest.raises(NineyardProtocolError, match="expected object"):
        list(NineyardCatalogReader(client).iter_items())
