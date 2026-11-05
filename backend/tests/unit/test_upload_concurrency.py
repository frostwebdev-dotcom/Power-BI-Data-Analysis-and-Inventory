"""A slow synchronous import must not starve the API's liveness probe."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import imports
from app.core.security import Principal, RoleCode
from app.services import imports as import_service
from app.services.imports import UploadEmpty


def test_health_responds_while_upload_pipeline_is_blocked(
    healthy_db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    actor = Principal(
        uuid.uuid4(),
        uuid.uuid4(),
        "operator@example.test",
        "Operator",
        frozenset({RoleCode.DATA_OPERATOR}),
    )
    healthy_db_app.dependency_overrides[imports._write.dependency] = lambda: actor

    def blocked_pipeline(*args: Any, **kwargs: Any) -> None:
        entered.set()
        assert release.wait(timeout=5), "test did not release the blocked upload"
        raise UploadEmpty()

    monkeypatch.setattr(import_service, "receive_upload", blocked_pipeline)
    with TestClient(healthy_db_app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(
            client.post,
            "/api/v1/imports",
            data={"vendor_id": str(uuid.uuid4())},
            files={"file": ("stock.csv", b"SKU,Qty\nA,1\n", "text/csv")},
        )
        try:
            assert entered.wait(timeout=2)
            health = pool.submit(client.get, "/health").result(timeout=2)
            assert health.status_code == 200
            assert not pending.done(), "health was checked after the upload finished"
        finally:
            release.set()
        assert pending.result(timeout=3).status_code == 422
