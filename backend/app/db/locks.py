"""Cross-process run locks held for longer than one transaction.

A scheduled sync, a manual CLI run and a second API revision during a deploy
may all try to start the same job. Each long job commits several times, so a
transaction-scoped lock is not enough: these helpers take a PostgreSQL
*session-level* advisory lock on a dedicated connection and hold it until the
``with`` block ends, whatever the job commits in between.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext

from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session


def advisory_lock_id(organization_id: uuid.UUID, key: str) -> int:
    """A stable signed 64-bit lock id for one job in one organization."""
    return int.from_bytes(
        hashlib.sha256(f"{organization_id}:{key}".encode()).digest()[:8], signed=True
    )


@contextmanager
def try_run_lock(session: Session, organization_id: uuid.UUID, key: str) -> Iterator[bool]:
    """Yield ``True`` if this process now holds the run lock, ``False`` if
    another one does. Never waits.

    A dedicated AUTOCOMMIT connection is used when the session is bound to an
    engine: committing the job's work must not return a session-level lock to
    the pool while the run is still active. A session bound to one connection
    (the test fixtures) uses that connection.
    """
    bind = session.get_bind()
    context = (
        bind.connect().execution_options(isolation_level="AUTOCOMMIT")
        if isinstance(bind, Engine)
        else nullcontext(bind)
    )
    lock_id = advisory_lock_id(organization_id, key)
    with context as connection:
        assert isinstance(connection, Connection)
        acquired = bool(
            connection.scalar(text("select pg_try_advisory_lock(:key)"), {"key": lock_id})
        )
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("select pg_advisory_unlock(:key)"), {"key": lock_id})
