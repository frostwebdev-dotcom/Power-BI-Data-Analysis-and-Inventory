"""The local storage backend: layout, immutability, integrity (ADR 0004)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import Settings
from app.imports.storage import (
    LocalStorageBackend,
    StorageIntegrityError,
    StorageObjectMissing,
    UnsupportedStorageUri,
    build_storage_backend,
    sha256_hex,
    split_uri,
    storage_key,
)

ORG = uuid.UUID("00000000-0000-0000-0000-00000000aaaa")
WHEN = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
CONTENT = b"UPC,Quantity\r\n012345678905,7\r\n"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def backend(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(tmp_path / "raw")


class TestKeyLayout:
    def test_organization_year_month_sha_extension(self) -> None:
        assert storage_key(ORG, DIGEST, "Weekly Prices.CSV", at=WHEN) == (
            f"{ORG}/2026/09/{DIGEST}.csv"
        )

    def test_unknown_extensions_become_bin(self) -> None:
        assert storage_key(ORG, DIGEST, "list.exe", at=WHEN).endswith(f"{DIGEST}.bin")
        assert storage_key(ORG, DIGEST, "noext", at=WHEN).endswith(f"{DIGEST}.bin")

    def test_only_the_suffix_of_a_path_is_used(self) -> None:
        assert storage_key(ORG, DIGEST, r"C:\Users\x\file.xlsx", at=WHEN).endswith(".xlsx")

    def test_split_uri(self) -> None:
        assert split_uri("local://a/b/c.csv") == ("local", "a/b/c.csv")
        assert split_uri("s3://bucket/a/b.csv") == ("s3", "bucket/a/b.csv")
        with pytest.raises(UnsupportedStorageUri):
            split_uri("/storage/raw/file.csv")


class TestPut:
    def test_writes_under_the_layout_and_returns_a_relative_uri(
        self, backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        uri = backend.put(CONTENT, "prices.csv", organization_id=ORG, at=WHEN)

        assert uri == f"local://{ORG}/2026/09/{DIGEST}.csv"
        stored = tmp_path / "raw" / str(ORG) / "2026" / "09" / f"{DIGEST}.csv"
        assert stored.read_bytes() == CONTENT
        assert backend.exists(uri)
        assert backend.open(uri) == CONTENT
        assert sha256_hex(backend.open(uri)) == DIGEST
        # No temporary file is left behind.
        assert [p.name for p in stored.parent.iterdir()] == [stored.name]

    def test_nothing_is_created_until_the_first_put(self, tmp_path: Path) -> None:
        LocalStorageBackend(tmp_path / "raw")
        assert not (tmp_path / "raw").exists()

    def test_the_same_bytes_are_stored_once(self, backend: LocalStorageBackend) -> None:
        first = backend.put(CONTENT, "a.csv", organization_id=ORG, at=WHEN)
        second = backend.put(CONTENT, "b.csv", organization_id=ORG, at=WHEN)

        assert first == second
        assert backend.open(first) == CONTENT

    def test_an_existing_object_is_never_overwritten(
        self, backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        """If something already sits at the key and does not hash to it, the
        put refuses rather than replacing it."""
        uri = backend.put(CONTENT, "a.csv", organization_id=ORG, at=WHEN)
        path = tmp_path / "raw" / split_uri(uri)[1]
        path.write_bytes(b"tampered")

        with pytest.raises(StorageIntegrityError, match="not overwritten"):
            backend.put(CONTENT, "a.csv", organization_id=ORG, at=WHEN)
        assert path.read_bytes() == b"tampered"

    def test_different_organizations_do_not_share_a_key(self, backend: LocalStorageBackend) -> None:
        other = uuid.uuid4()
        a = backend.put(CONTENT, "a.csv", organization_id=ORG, at=WHEN)
        b = backend.put(CONTENT, "a.csv", organization_id=other, at=WHEN)
        assert a != b and backend.open(a) == backend.open(b) == CONTENT

    def test_binary_content_round_trips_exactly(self, backend: LocalStorageBackend) -> None:
        content = bytes(range(256)) * 1000 + b"\xef\xbb\xbf\r\n\x1a"
        uri = backend.put(content, "x.xlsx", organization_id=ORG)
        assert backend.open(uri) == content


class TestOpen:
    def test_missing_object(self, backend: LocalStorageBackend) -> None:
        uri = f"local://{ORG}/2026/09/{'0' * 64}.csv"
        assert not backend.exists(uri)
        with pytest.raises(StorageObjectMissing):
            backend.open(uri)

    def test_another_backends_uri_is_refused(self, backend: LocalStorageBackend) -> None:
        with pytest.raises(UnsupportedStorageUri, match="'s3'"):
            backend.open("s3://bucket/key.csv")

    def test_a_key_cannot_escape_the_root(self, backend: LocalStorageBackend) -> None:
        with pytest.raises(UnsupportedStorageUri, match="escapes"):
            backend.open("local://../../etc/passwd")


def test_build_storage_backend_uses_the_raw_dir(tmp_path: Path) -> None:
    settings = Settings(app_env="test", storage_raw_dir=str(tmp_path / "somewhere"))
    backend = build_storage_backend(settings)
    assert isinstance(backend, LocalStorageBackend)
    assert backend.root == tmp_path / "somewhere"
