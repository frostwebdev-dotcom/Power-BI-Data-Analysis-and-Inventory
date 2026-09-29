"""Read-only Nineyard API diagnostic.

Run from the ``backend`` directory:

    python -m app.cli.nineyard_probe --dry-run          # makes no network call
    python -m app.cli.nineyard_probe                    # reads the four endpoints

This tool only ever reads. The client underneath has one data method and no HTTP
verb parameter, so there is no way to reach an inventory-update or any other
mutating endpoint from here.

Credentials come from ``.env`` and nowhere else. The email, the password and the
token are never printed, never logged, and never written to a sample file.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.integrations.nineyard.client import AUTH_PATH, NineyardClient, NineyardConfig
from app.integrations.nineyard.errors import NineyardConfigurationError
from app.integrations.nineyard.probe import (
    READ_ONLY_ENDPOINT_DEFAULT_PARAMS,
    READ_ONLY_ENDPOINTS,
    ProbeReport,
    run_probe,
)
from app.integrations.nineyard.sanitize import DEFAULT_SAMPLE_SIZE

#: Under storage/, which .gitignore excludes wholesale. Samples are sanitised
#: anyway; being unable to commit them is the second line of defence.
#: Resolved from this file rather than the working directory, so the output lands
#: in the same place regardless of where the command was run from.
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "storage" / "diagnostics" / "nineyard"

EXIT_OK = 0
EXIT_CONFIGURATION = 1
EXIT_AUTHENTICATION = 2
EXIT_ENDPOINT_FAILURES = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.nineyard_probe",
        description="Read-only Nineyard API diagnostic. Never writes to Nineyard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Endpoints inspected (GET only):\n"
            + "\n".join(f"  {name:<16} {path}" for name, path in READ_ONLY_ENDPOINTS.items())
            + f"\n\nAuthentication: POST {AUTH_PATH}\n"
            "\nCredentials are read from NINEYARD_EMAIL, NINEYARD_PASSWORD and\n"
            "NINEYARD_COMPANY_ID in .env. They are never echoed."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print exactly what would be requested and exit without any network call.",
    )
    parser.add_argument(
        "--endpoints",
        default=",".join(READ_ONLY_ENDPOINTS),
        help=f"Comma-separated subset of: {', '.join(READ_ONLY_ENDPOINTS)}. Default: all.",
    )
    parser.add_argument(
        "--save-samples",
        action="store_true",
        help="Write the sanitised report to --output-dir (git-ignored).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Where samples are written. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"Records described per endpoint. Default: {DEFAULT_SAMPLE_SIZE}",
    )
    parser.add_argument("--base-url", help="Override NINEYARD_BASE_URL for this run.")
    parser.add_argument("--timeout", type=float, help="Override the request timeout in seconds.")
    parser.add_argument(
        "--max-attempts",
        type=int,
        help="Attempts per request. Retries apply to transient failures only.",
    )
    parser.add_argument(
        "--page-param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Extra query parameter, repeatable. Useful for probing paging once a "
            "candidate parameter name is known, e.g. --page-param pageSize=5"
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON on stdout.")
    return parser


def resolve_endpoints(selected: str) -> dict[str, str]:
    names = [name.strip() for name in selected.split(",") if name.strip()]
    unknown = [name for name in names if name not in READ_ONLY_ENDPOINTS]
    if unknown:
        raise SystemExit(
            f"Unknown endpoint(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(READ_ONLY_ENDPOINTS)}"
        )
    return {name: READ_ONLY_ENDPOINTS[name] for name in names}


def parse_params(pairs: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise SystemExit(f"--page-param expects KEY=VALUE, got: {pair!r}")
        params[key.strip()] = value.strip()
    return params


def build_config(settings: Settings, args: argparse.Namespace) -> NineyardConfig:
    config = NineyardConfig.from_settings(settings)
    overrides: dict[str, Any] = {}
    if args.base_url:
        overrides["base_url"] = str(args.base_url).rstrip("/")
    if args.timeout is not None:
        overrides["timeout_seconds"] = args.timeout
    if args.max_attempts is not None:
        overrides["max_attempts"] = args.max_attempts
    return replace_config(config, overrides)


def replace_config(config: NineyardConfig, overrides: dict[str, Any]) -> NineyardConfig:
    if not overrides:
        return config
    return dataclasses.replace(config, **overrides)


def print_dry_run(settings: Settings, endpoints: dict[str, str], params: dict[str, str]) -> None:
    """Show the exact request plan without making any of them."""
    base = settings.nineyard_base_url.rstrip("/")
    credentials_present = settings.has_nineyard_credentials

    print("Nineyard probe - DRY RUN. No network request will be made.\n")
    print(f"  Base URL          : {base}")
    print(f"  Credentials in env: {'yes' if credentials_present else 'NO - see below'}")
    print(f"  Company id        : {settings.nineyard_company_id or '(unset)'}")
    print(f"  Timeout           : {settings.nineyard_timeout_seconds}s")
    print(f"  Attempts          : {settings.nineyard_max_attempts} (transient failures only)")
    print("\nRequests that would be made, in order:\n")
    print(f"  1. POST {base}{AUTH_PATH}")
    print("     body: email, password, companyId   (never logged or printed)")
    for index, (name, path) in enumerate(endpoints.items(), start=2):
        endpoint_params = dict(READ_ONLY_ENDPOINT_DEFAULT_PARAMS.get(name, {}))
        endpoint_params.update(params)
        query = (
            f"?{'&'.join(f'{k}={v}' for k, v in endpoint_params.items())}"
            if endpoint_params
            else ""
        )
        print(f"  {index}. GET  {base}{path}{query}   [{name}]")
    print("\nNo other request is possible: the client exposes no mutating verb.")

    if not credentials_present:
        print(
            "\nCredentials are not set. Add to .env:\n"
            "  NINEYARD_EMAIL=you@example.com\n"
            "  NINEYARD_PASSWORD=your-password\n"
            "  NINEYARD_COMPANY_ID=1234"
        )


def format_report(report: ProbeReport) -> str:
    lines: list[str] = []
    lines.append(f"Nineyard probe - {report.base_url}")
    lines.append("=" * 72)

    if not report.authenticated:
        lines.append(f"\nAUTHENTICATION FAILED\n  {report.auth_error}")
        if report.auth_error_guidance:
            lines.append(f"  -> {report.auth_error_guidance}")
        return "\n".join(lines)

    lines.append("\nAuthentication: OK")
    lines.append(f"  token fingerprint : {report.token_fingerprint}  (hash prefix, not the token)")
    lines.append(f"  expiresIn         : {_shown(report.token_expires_in)}")
    lines.append(f"  expires           : {_shown(report.token_expires)}")
    lines.append(f"  response keys     : {', '.join(report.token_response_keys) or '(none)'}")

    for endpoint in report.endpoints:
        lines.append("")
        lines.append("-" * 72)
        lines.append(f"{endpoint.name}  ({endpoint.path})")
        if endpoint.error_type:
            lines.append(f"  FAILED  status={_shown(endpoint.status)}  {endpoint.error_type}")
            lines.append(f"  {endpoint.error_message}")
            if endpoint.error_guidance:
                lines.append(f"  -> {endpoint.error_guidance}")
            continue

        lines.append(f"  status            : {endpoint.status}")
        lines.append(f"  content-type      : {_shown(endpoint.content_type)}")
        lines.append(f"  elapsed           : {endpoint.elapsed_ms} ms")
        lines.append(f"  top-level type    : {_shown(endpoint.top_level_type)}")
        if endpoint.top_level_keys:
            lines.append(f"  top-level keys    : {', '.join(endpoint.top_level_keys)}")
        lines.append(
            f"  record container  : {_shown(endpoint.record_container_key, '(bare array)')}"
        )
        lines.append(f"  record count      : {_shown(endpoint.record_count)}")
        lines.append(f"  paging candidates : {json.dumps(endpoint.pagination_candidates) or '{}'}")
        if endpoint.record_field_names:
            lines.append(f"  fields ({len(endpoint.record_field_names)}):")
            total = endpoint.record_count or 0
            for name in endpoint.record_field_names:
                seen = endpoint.record_field_presence.get(name, 0)
                marker = "" if seen == total else f"  (in {seen}/{total} records)"
                lines.append(f"    - {name}{marker}")
        else:
            lines.append("  fields            : none observed (no records returned)")

    lines.append("")
    lines.append("=" * 72)
    succeeded = sum(1 for endpoint in report.endpoints if endpoint.succeeded)
    lines.append(f"{succeeded}/{len(report.endpoints)} endpoints returned a readable response.")
    return "\n".join(lines)


def _shown(value: Any, fallback: str = "(absent)") -> str:
    return fallback if value is None else str(value)


def save_report(report: ProbeReport, output_dir: Path) -> Path:
    """Write the sanitised report. Contains no real values by construction."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = output_dir / f"nineyard-probe-{stamp}.json"
    destination.write_text(json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8")
    return destination


def exit_code_for(report: ProbeReport) -> int:
    if not report.authenticated:
        return EXIT_AUTHENTICATION
    if any(not endpoint.succeeded for endpoint in report.endpoints):
        return EXIT_ENDPOINT_FAILURES
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings)

    endpoints = resolve_endpoints(args.endpoints)
    params = parse_params(args.page_param)

    if args.dry_run:
        print_dry_run(settings, endpoints, params)
        return EXIT_OK

    try:
        config = build_config(settings, args)
    except NineyardConfigurationError as exc:
        print(f"Configuration error: {exc.message}\n\n{exc.guidance}", file=sys.stderr)
        return EXIT_CONFIGURATION

    with NineyardClient(config) as client:
        report = run_probe(
            client, endpoints=endpoints, params=params or None, sample_size=args.sample_size
        )

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, default=str))
    else:
        print(format_report(report))

    if args.save_samples:
        destination = save_report(report, args.output_dir)
        print(f"\nSanitised report written to {destination}", file=sys.stderr)

    return exit_code_for(report)


if __name__ == "__main__":
    raise SystemExit(main())
