"""The Amazon proof-of-concept command line (ADR 0011).

    python -m app.cli.amazon_poc auth
    python -m app.cli.amazon_poc sync-orders [--days N]
    python -m app.cli.amazon_poc sync-inventory
    python -m app.cli.amazon_poc sync-listings
    python -m app.cli.amazon_poc run
    python -m app.cli.amazon_poc velocity [--level sku|product] [--top N]

Every subcommand reads credentials from the environment (``AMAZON_*``),
never from an argument, and never prints, logs or stores any of them.
``auth`` prints the access token's remaining lifetime and nothing else.
The sync commands are the same services the scheduler runs, triggered as
``MANUAL``; a run that ends ``FAILED`` makes the process exit non-zero so a
shell or a CI step can tell.

Exit codes: 0 success · 1 a run FAILED · 2 configuration / credentials ·
3 unexpected error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.integrations.amazon import AmazonAuthError, AmazonClient, AmazonConfig, AmazonError
from app.integrations.amazon.errors import AmazonConfigurationError
from app.models.amazon import AmazonSyncRun
from app.models.enums import AmazonSyncJobType, SyncStatus, TriggerType
from app.services.amazon_runs import AmazonOrganizationError, SyncAlreadyRunning
from app.services.amazon_velocity import VelocityRow

_logger = get_logger(__name__)

EXIT_OK: Final = 0
EXIT_RUN_FAILED: Final = 1
EXIT_CONFIGURATION: Final = 2
EXIT_UNEXPECTED: Final = 3

#: Longest trailing window a single ``sync-orders`` accepts. Longer windows
#: are split into report-sized runs, exactly as the scheduled job does.
DEFAULT_ORDER_DAYS: Final = 35

VELOCITY_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("seller_sku", "Seller SKU"),
    ("asin", "ASIN"),
    ("catalog_item_number", "Catalog Item #"),
    ("upc", "UPC"),
    ("units_7", "Units 7d"),
    ("units_14", "Units 14d"),
    ("units_30", "Units 30d"),
    ("avg_daily_14", "Avg/day 14d"),
    ("fulfillable", "Fulfillable"),
    ("fbm_quantity", "FBM"),
    ("inbound", "Inbound"),
    ("days_of_supply", "Days supply"),
    ("mapping_method", "Mapping"),
)

#: Columns whose values are numbers and therefore right-aligned.
NUMERIC_COLUMNS: Final = frozenset(
    {
        "units_7",
        "units_14",
        "units_30",
        "avg_daily_14",
        "fulfillable",
        "fbm_quantity",
        "inbound",
        "days_of_supply",
    }
)


# --- argument parsing --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amazon_poc",
        description="Amazon SP-API proof of concept: read-only ingestion and sales velocity.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Credentials are read from AMAZON_* environment variables only and are never "
            "printed. Exit codes: 0 ok, 1 a run FAILED, 2 configuration, 3 unexpected."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("auth", help="Exchange the refresh token and print the token's expiry.")

    orders = commands.add_parser("sync-orders", help="Pull the trailing orders window.")
    orders.add_argument(
        "--order-date-baseline",
        action="store_true",
        help="Explicitly request an order-date baseline instead of the default update-date report.",
    )
    orders.add_argument(
        "--days",
        type=positive_int,
        default=DEFAULT_ORDER_DAYS,
        help=f"Trailing window in days (default {DEFAULT_ORDER_DAYS}); split into 30-day reports.",
    )

    commands.add_parser("sync-inventory", help="Pull FBA inventory and FBM quantities.")
    commands.add_parser("sync-listings", help="Pull listings and map them to products.")

    run = commands.add_parser("run", help="Orders, inventory, then listings, in that order.")
    run.add_argument("--days", type=positive_int, default=DEFAULT_ORDER_DAYS)

    velocity = commands.add_parser("velocity", help="Print the sales-velocity table.")
    velocity.add_argument("--level", choices=("sku", "product"), default="sku")
    velocity.add_argument("--top", type=positive_int, default=None, help="Only the first N rows.")

    return parser


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


# --- table formatting ---------------------------------------------------------------


def format_cell(row: VelocityRow, key: str) -> str:
    value: Any = getattr(row, key)
    if value is None:
        return "-"
    if key == "mapping_method":
        return value.value if hasattr(value, "value") else str(value)
    if key in ("avg_daily_14", "days_of_supply"):
        return f"{value:.1f}"
    return str(value)


def format_velocity_table(rows: Sequence[VelocityRow]) -> str:
    """An aligned plain-text table. Numbers right-aligned, text left-aligned."""
    headers = [header for _, header in VELOCITY_COLUMNS]
    keys = [key for key, _ in VELOCITY_COLUMNS]
    cells = [[format_cell(row, key) for key in keys] for row in rows]
    widths = [
        max(len(header), *(len(line[i]) for line in cells)) if cells else len(header)
        for i, header in enumerate(headers)
    ]

    def render(line: Sequence[str]) -> str:
        parts = []
        for i, (key, text) in enumerate(zip(keys, line, strict=True)):
            parts.append(text.rjust(widths[i]) if key in NUMERIC_COLUMNS else text.ljust(widths[i]))
        return "  ".join(parts).rstrip()

    lines = [render(headers), "  ".join("-" * w for w in widths)]
    lines.extend(render(line) for line in cells)
    if not cells:
        lines.append("(no data — run sync-orders and sync-inventory first)")
    return "\n".join(lines)


# --- commands ---------------------------------------------------------------------


@dataclass(slots=True)
class Outcome:
    exit_code: int
    lines: list[str]


def command_auth(settings: Settings) -> Outcome:
    client = AmazonClient(AmazonConfig.from_settings(settings))
    check = client.check_credentials()
    if check.expires_in_seconds is None:
        return Outcome(EXIT_OK, ["token obtained; expiry not reported"])
    expires_at = datetime.now(UTC) + timedelta(seconds=check.expires_in_seconds)
    return Outcome(
        EXIT_OK,
        [f"token obtained; expires in {check.expires_in_seconds}s at {expires_at.isoformat()}"],
    )


def _open_session() -> Any:
    from app.db.session import get_session_factory

    return get_session_factory()()


def _resolve_organization(session: Any, settings: Settings) -> Any:
    from app.services.amazon_runs import resolve_amazon_organization

    return resolve_amazon_organization(session, settings.amazon_organization_slug)


def _report_run(run: AmazonSyncRun, label: str) -> str:
    return (
        f"{label}: {run.status.value} — seen {run.rows_seen}, created {run.rows_created}, "
        f"updated {run.rows_updated}, unchanged {run.rows_unchanged}, failed {run.rows_failed}"
        + (f" — {run.error_message}" if run.error_message else "")
    )


def _exit_for(runs: Sequence[AmazonSyncRun]) -> int:
    return EXIT_RUN_FAILED if any(r.status is SyncStatus.FAILED for r in runs) else EXIT_OK


def command_sync_orders(
    settings: Settings, days: int, *, order_date_baseline: bool = False
) -> Outcome:
    from app.jobs.amazon import orders_windows, prepare_run
    from app.services.amazon_orders import run_orders_sync

    session = _open_session()
    try:
        now = datetime.now(UTC)
        organization_id = prepare_run(session, settings, AmazonSyncJobType.ORDERS_REPORT, now)
        runs: list[AmazonSyncRun] = []
        lines: list[str] = []
        for window_start, window_end in orders_windows(days, now=now):
            try:
                if order_date_baseline:
                    run = run_orders_sync(
                        session,
                        organization_id,
                        window_start,
                        window_end,
                        TriggerType.MANUAL,
                        report_basis="ORDER_DATE",
                    )
                else:
                    run = run_orders_sync(
                        session, organization_id, window_start, window_end, TriggerType.MANUAL
                    )
            except SyncAlreadyRunning as exc:
                return Outcome(EXIT_RUN_FAILED, [str(exc)])
            except Exception as exc:  # the run is already FAILED; report and stop
                lines.append(
                    f"orders {window_start:%Y-%m-%d}..{window_end:%Y-%m-%d}: FAILED — {exc}"
                )
                return Outcome(EXIT_RUN_FAILED, lines)
            runs.append(run)
            lines.append(_report_run(run, f"orders {window_start:%Y-%m-%d}..{window_end:%Y-%m-%d}"))
        return Outcome(_exit_for(runs), lines)
    finally:
        session.close()


def _simple_sync(
    settings: Settings,
    job_type: AmazonSyncJobType,
    label: str,
    runner: Callable[[Any, Any], AmazonSyncRun],
) -> Outcome:
    session = _open_session()
    try:
        organization_id = prepare_run(session, settings, job_type, datetime.now(UTC))
        try:
            run = runner(session, organization_id)
        except SyncAlreadyRunning as exc:
            return Outcome(EXIT_RUN_FAILED, [str(exc)])
        except Exception as exc:
            return Outcome(EXIT_RUN_FAILED, [f"{label}: FAILED — {exc}"])
        return Outcome(_exit_for([run]), [_report_run(run, label)])
    finally:
        session.close()


def prepare_run(
    session: Any, settings: Settings, job_type: AmazonSyncJobType, now: datetime
) -> Any:
    from app.jobs.amazon import prepare_run as _prepare

    return _prepare(session, settings, job_type, now)


def command_sync_inventory(settings: Settings) -> Outcome:
    from app.services.amazon_inventory import run_inventory_sync

    return _simple_sync(
        settings,
        AmazonSyncJobType.FBA_INVENTORY,
        "inventory",
        lambda session, org: run_inventory_sync(session, org, TriggerType.MANUAL),
    )


def command_sync_listings(settings: Settings) -> Outcome:
    from app.services.amazon_listings import run_listings_sync

    return _simple_sync(
        settings,
        AmazonSyncJobType.LISTINGS_REPORT,
        "listings",
        lambda session, org: run_listings_sync(session, org, TriggerType.MANUAL),
    )


def command_run(settings: Settings, days: int) -> Outcome:
    """Orders, inventory, listings — in that order, stopping at the first failure."""
    lines: list[str] = []
    steps: list[Callable[[], Outcome]] = [
        lambda: command_sync_orders(settings, days),
        lambda: command_sync_inventory(settings),
        lambda: command_sync_listings(settings),
    ]
    for step in steps:
        outcome = step()
        lines.extend(outcome.lines)
        if outcome.exit_code != EXIT_OK:
            return Outcome(outcome.exit_code, lines)
    return Outcome(EXIT_OK, lines)


def command_velocity(settings: Settings, level: str, top: int | None) -> Outcome:
    from app.services.amazon_velocity import velocity_report

    session = _open_session()
    try:
        organization_id = _resolve_organization(session, settings)
        rows = velocity_report(session, organization_id, level=level, top=top)  # type: ignore[arg-type]
        return Outcome(EXIT_OK, [format_velocity_table(rows)])
    finally:
        session.close()


# --- entry point --------------------------------------------------------------------


def dispatch(args: argparse.Namespace, settings: Settings) -> Outcome:
    if args.command == "auth":
        return command_auth(settings)
    if args.command == "sync-orders":
        if args.order_date_baseline:
            return command_sync_orders(settings, args.days, order_date_baseline=True)
        return command_sync_orders(settings, args.days)
    if args.command == "sync-inventory":
        return command_sync_inventory(settings)
    if args.command == "sync-listings":
        return command_sync_listings(settings)
    if args.command == "run":
        return command_run(settings, args.days)
    if args.command == "velocity":
        return command_velocity(settings, args.level, args.top)
    raise AssertionError(f"unhandled command {args.command!r}")  # pragma: no cover


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings)

    try:
        outcome = dispatch(args, settings)
    except (AmazonConfigurationError, AmazonOrganizationError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION
    except AmazonAuthError as exc:
        print(f"authentication failed: {exc.message}", file=sys.stderr)
        print(exc.guidance, file=sys.stderr)
        return EXIT_CONFIGURATION
    except AmazonError as exc:
        print(f"Amazon error: {exc.message}", file=sys.stderr)
        print(exc.guidance, file=sys.stderr)
        return EXIT_RUN_FAILED
    except Exception as exc:
        _logger.exception("amazon_poc.unexpected")
        print(f"unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_UNEXPECTED

    for line in outcome.lines:
        print(line)
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())
