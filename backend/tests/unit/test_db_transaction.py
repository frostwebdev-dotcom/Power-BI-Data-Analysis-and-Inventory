"""The non-transactional session lifecycle used by long-running sync jobs."""

from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy.orm import Session

from app.db.transaction import session_lifecycle


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_session_lifecycle_only_closes_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    monkeypatch.setattr("app.db.transaction.get_session_factory", lambda: lambda: session)

    with session_lifecycle() as opened:
        assert opened is cast(Session, session)
        assert session.closed is False

    assert session.closed is True


def test_session_lifecycle_closes_after_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    monkeypatch.setattr("app.db.transaction.get_session_factory", lambda: lambda: session)

    with pytest.raises(RuntimeError, match="remote failure"), session_lifecycle():
        raise RuntimeError("remote failure")

    assert session.closed is True
