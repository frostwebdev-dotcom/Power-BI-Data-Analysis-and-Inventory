"""Fixtures for database-backed tests.

These tests run against a real PostgreSQL, because most of what is being
asserted — partial unique indexes, check constraints, ``ON DELETE`` behaviour,
native enums, triggers — does not exist outside PostgreSQL and cannot be
meaningfully faked.

The schema is created by running the Alembic migrations, not
``metadata.create_all``. That way the tests verify the migration developers
actually deploy, rather than a parallel construction that could drift from it
(CLAUDE.md §4).

If no database is reachable, every test in this package skips with an
explanation rather than failing, so the unit suite still runs anywhere.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from tests.integration.paths import ALEMBIC_INI, BACKEND_ROOT

# Kept out of the default database name so a stray misconfiguration can never
# point the destructive fixtures at a development database.
TEST_DATABASE_SUFFIX = "_test"


def resolve_test_database_url() -> URL:
    """Work out which database the tests should use.

    ``TEST_DATABASE_URL`` wins if set. Otherwise the configured application
    database name gains a ``_test`` suffix, so running the suite can never touch
    development data.
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return make_url(explicit)

    configured = os.environ.get("DATABASE_URL") or get_settings().database_url.get_secret_value()
    base = make_url(configured)
    database = base.database or "prms"
    if database.endswith(TEST_DATABASE_SUFFIX):
        return base
    return base.set(database=f"{database}{TEST_DATABASE_SUFFIX}")


def admin_url(url: URL) -> URL:
    """A URL for the maintenance database, used to CREATE/DROP DATABASE."""
    return url.set(database="postgres")


def database_exists(url: URL) -> bool:
    engine = create_engine(admin_url(url), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            found = connection.execute(
                text("select 1 from pg_database where datname = :name"),
                {"name": url.database},
            ).scalar()
        return found is not None
    finally:
        engine.dispose()


def create_database(url: URL) -> None:
    engine = create_engine(admin_url(url), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'create database "{url.database}"'))
    finally:
        engine.dispose()


def drop_database(url: URL) -> None:
    engine = create_engine(admin_url(url), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    # Client sessions only: an autovacuum worker on the scratch
                    # database runs as a superuser, and terminating it is a
                    # permission error for the test role (the intermittent
                    # setup failure this suite used to show).
                    "select pg_terminate_backend(pid) from pg_stat_activity"
                    " where datname = :name and pid <> pg_backend_pid()"
                    " and backend_type = 'client backend'"
                ),
                {"name": url.database},
            )
            connection.execute(text(f'drop database if exists "{url.database}"'))
    finally:
        engine.dispose()


def alembic_config(url: URL) -> Config:
    """Alembic configuration pointed at a specific database.

    ``script_location`` is made absolute so the tests work regardless of the
    directory pytest was invoked from.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    return config


def run_migrations(url: URL, revision: str = "head") -> None:
    """Apply migrations to ``url``.

    ``alembic/env.py`` reads the URL from application settings, so the
    environment variable is set for the duration of the call and the settings
    cache cleared around it. Setting it on the Config alone would be silently
    overridden.
    """
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        command.upgrade(alembic_config(url), revision)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


def check_migrations(url: URL) -> None:
    """Run ``alembic check`` against ``url``.

    Raises ``AutogenerateDiffsDetected`` if the models and the applied migration
    disagree.
    """
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        command.check(alembic_config(url))
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


def downgrade_migrations(url: URL, revision: str = "base") -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        command.downgrade(alembic_config(url), revision)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def test_database_url() -> URL:
    return resolve_test_database_url()


@pytest.fixture(scope="session")
def migrated_engine(test_database_url: URL) -> Iterator[Engine]:
    """A migrated test database, created once per session.

    Skips the whole package when PostgreSQL is unreachable — a missing database
    is an environment gap, not a failing assertion.
    """
    try:
        exists = database_exists(test_database_url)
    except OperationalError as exc:
        pytest.skip(
            "PostgreSQL is not reachable at "
            f"{test_database_url.set(password=None).render_as_string()}: {exc.orig}"
        )

    if not exists:
        create_database(test_database_url)

    run_migrations(test_database_url)

    engine = create_engine(test_database_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """A session whose work is always rolled back.

    The session joins an outer transaction using savepoints, so a test may call
    ``commit()`` — and may provoke an ``IntegrityError`` and recover from it —
    while the database is left untouched afterwards.
    """
    connection = migrated_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
