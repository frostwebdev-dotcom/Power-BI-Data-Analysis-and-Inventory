"""Where retained vendor files live (ADR 0004).

A ``StorageBackend`` stores bytes once and hands them back unchanged. The
API and the services never touch a filesystem or a bucket directly; they
hold a URI on ``import_files.storage_uri`` and ask the backend for it.

**URI design.** A storage URI is ``<scheme>://<key>``. The key is the
object's path *within* the backend, never an absolute path on a host, so
the local root can move (a different bind mount, a different machine) and
the rows stay valid. An S3 or Azure backend would mint ``s3://bucket/key``
or ``azure://container/key`` with the same key layout; adding one is a new
class plus a case in :func:`build_storage_backend`, and no schema change.

The key layout, ``<organization_id>/<yyyy>/<mm>/<sha256>.<ext>``, is
content-addressed: the same bytes always land at the same key, so a retry
after a crash between "stored" and "recorded" finds the object already
there and nothing is ever written twice or written over.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final, Protocol

from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)

LOCAL_SCHEME: Final = "local"
#: Extensions the key keeps; anything else is stored as ``.bin`` so the key
#: never carries an attacker-chosen suffix.
KEPT_EXTENSIONS: Final = frozenset({".csv", ".xlsx"})


class StorageError(Exception):
    """The backend could not do what was asked."""


class StorageObjectMissing(StorageError):  # noqa: N818 — an outcome, not a category
    """A URI the database points at has no object behind it."""


class StorageIntegrityError(StorageError):
    """An object exists at the key but its bytes do not hash to the key."""


class UnsupportedStorageUri(StorageError):  # noqa: N818
    """The URI's scheme belongs to a backend this process does not have."""


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def storage_key(
    organization_id: uuid.UUID, digest: str, suggested_name: str, *, at: datetime
) -> str:
    """``<organization_id>/<yyyy>/<mm>/<sha256>.<ext>``, always POSIX-separated."""
    suffix = PurePosixPath(suggested_name.replace("\\", "/")).suffix.lower()
    ext = suffix if suffix in KEPT_EXTENSIONS else ".bin"
    return f"{organization_id}/{at:%Y}/{at:%m}/{digest}{ext}"


def split_uri(storage_uri: str) -> tuple[str, str]:
    scheme, separator, key = storage_uri.partition("://")
    if not separator or not scheme or not key:
        raise UnsupportedStorageUri(f"not a storage URI: {storage_uri!r}")
    return scheme, key


class StorageBackend(Protocol):
    def put(
        self,
        content: bytes,
        suggested_name: str,
        *,
        organization_id: uuid.UUID,
        at: datetime | None = None,
    ) -> str:
        """Store ``content`` and return its URI. Never overwrites."""
        ...

    def open(self, storage_uri: str) -> bytes:
        """The exact bytes stored under ``storage_uri``."""
        ...

    def exists(self, storage_uri: str) -> bool: ...


class LocalStorageBackend:
    """Files under one root directory, keyed as :func:`storage_key` says.

    Writes go to a temporary sibling first and are then hard-linked to the
    final name, which fails if the name is already taken — so two uploads
    of the same bytes cannot race each other into a torn file, and nothing
    is ever overwritten. Where the filesystem refuses hard links the write
    falls back to a rename guarded by an existence check.
    """

    scheme = LOCAL_SCHEME

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    # --- StorageBackend ------------------------------------------------------------

    def put(
        self,
        content: bytes,
        suggested_name: str,
        *,
        organization_id: uuid.UUID,
        at: datetime | None = None,
    ) -> str:
        digest = sha256_hex(content)
        key = storage_key(organization_id, digest, suggested_name, at=at or datetime.now(UTC))
        target = self._path(key)
        uri = f"{self.scheme}://{key}"

        if target.exists():
            self._verify(target, digest)
            return uri

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            self._place(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        self._verify(target, digest)
        _logger.info("storage.put", uri=uri, size_bytes=len(content))
        return uri

    def open(self, storage_uri: str) -> bytes:
        path = self._resolve(storage_uri)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageObjectMissing(f"no object at {storage_uri}") from exc

    def exists(self, storage_uri: str) -> bool:
        return self._resolve(storage_uri).is_file()

    # --- internals -----------------------------------------------------------------

    def _path(self, key: str) -> Path:
        path = (self.root / Path(*key.split("/"))).resolve()
        if self.root.resolve() not in path.parents:
            raise UnsupportedStorageUri(f"key escapes the storage root: {key!r}")
        return path

    def _resolve(self, storage_uri: str) -> Path:
        scheme, key = split_uri(storage_uri)
        if scheme != self.scheme:
            raise UnsupportedStorageUri(
                f"{storage_uri!r} belongs to a {scheme!r} backend; this process has {self.scheme!r}"
            )
        return self._path(key)

    @staticmethod
    def _place(temporary: Path, target: Path) -> None:
        try:
            os.link(temporary, target)
            return
        except FileExistsError:
            return  # someone stored the same bytes first; _verify checks them
        except OSError:
            pass  # no hard links here (some mounted volumes); fall back
        if target.exists():
            return
        temporary.replace(target)

    @staticmethod
    def _verify(target: Path, digest: str) -> None:
        actual = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                actual.update(chunk)
        if actual.hexdigest() != digest:
            raise StorageIntegrityError(
                f"object at {target.name} does not hash to its key; it was not overwritten"
            )


def build_storage_backend(settings: Settings) -> StorageBackend:
    """The one place a backend is chosen. Local filesystem today (B5 open)."""
    return LocalStorageBackend(settings.storage_raw_dir)
