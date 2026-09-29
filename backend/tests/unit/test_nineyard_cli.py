"""The probe CLI.

The load-bearing test here is the last one: ``--dry-run`` must open no socket at
all. Everything else about this tool is a safety story, and a dry run that
quietly made a request would undermine all of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.cli import nineyard_probe as cli
from app.core.config import Settings
from app.integrations.nineyard.probe import EndpointReport, ProbeReport


def configured_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "app_env": "test",
        "nineyard_base_url": "https://backyard.nineyard.test",
        "nineyard_email": "probe@example.test",
        "nineyard_password": SecretStr("probe-password"),
        "nineyard_company_id": 4321,
    }
    return Settings(**{**defaults, **overrides})


class TestArgumentHandling:
    def test_all_endpoints_are_selected_by_default(self) -> None:
        args = cli.build_parser().parse_args([])

        assert set(cli.resolve_endpoints(args.endpoints)) == {
            "Items",
            "Skus",
            "Vendors",
            "PurchaseOrders",
        }

    def test_a_subset_can_be_selected(self) -> None:
        assert set(cli.resolve_endpoints("Items,Vendors")) == {"Items", "Vendors"}

    def test_an_unknown_endpoint_is_refused(self) -> None:
        """Including anything that smells like a write."""
        with pytest.raises(SystemExit, match="Unknown endpoint"):
            cli.resolve_endpoints("UpdateInventory")

    def test_query_parameters_are_parsed(self) -> None:
        assert cli.parse_params(["pageSize=5", "page=1"]) == {"pageSize": "5", "page": "1"}

    def test_a_malformed_parameter_is_refused(self) -> None:
        with pytest.raises(SystemExit, match="KEY=VALUE"):
            cli.parse_params(["pageSize"])


class TestDryRun:
    def test_it_prints_the_exact_request_plan(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "get_settings", configured_settings)

        exit_code = cli.main(["--dry-run"])

        output = capsys.readouterr().out
        assert exit_code == cli.EXIT_OK
        assert "DRY RUN" in output
        assert "POST https://backyard.nineyard.test/api/OAuth/UsernameToken" in output
        for path in ("/api/Items", "/api/Skus", "/api/Vendors", "/api/PurchaseOrders"):
            assert f"GET  https://backyard.nineyard.test{path}" in output
        assert "/api/Items?Page=1&PerPage=3" in output
        assert "/api/Skus?PageNumber=1" in output

    def test_it_never_prints_the_credentials(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "get_settings", configured_settings)

        cli.main(["--dry-run"])

        output = capsys.readouterr().out
        assert "probe@example.test" not in output
        assert "probe-password" not in output

    def test_it_explains_how_to_supply_missing_credentials(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            cli,
            "get_settings",
            lambda: configured_settings(nineyard_email=None, nineyard_password=None),
        )

        cli.main(["--dry-run"])

        output = capsys.readouterr().out
        assert "NINEYARD_EMAIL" in output
        assert "NINEYARD_PASSWORD" in output

    def test_a_dry_run_opens_no_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The guarantee the whole tool rests on.

        Any attempt to construct an HTTP client during a dry run fails the test
        loudly rather than silently reaching the internet.
        """
        monkeypatch.setattr(cli, "get_settings", configured_settings)

        def explode(*_: Any, **__: Any) -> None:
            raise AssertionError("--dry-run attempted a network client")

        monkeypatch.setattr("app.integrations.nineyard.client.httpx.Client", explode)

        assert cli.main(["--dry-run"]) == cli.EXIT_OK


class TestConfigurationFailure:
    def test_missing_credentials_exit_with_a_configuration_code(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "get_settings", lambda: configured_settings(nineyard_email=None))

        exit_code = cli.main([])

        assert exit_code == cli.EXIT_CONFIGURATION
        assert "NINEYARD_EMAIL" in capsys.readouterr().err


class TestReportFormatting:
    @staticmethod
    def _report(**overrides: Any) -> ProbeReport:
        report = ProbeReport(
            base_url="https://backyard.nineyard.test",
            authenticated=True,
            token_fingerprint="abc123def456",
            token_expires_in=3600,
            token_expires="2026-09-08T18:00:00Z",
            token_response_keys=["accessToken", "expires", "expiresIn"],
        )
        for key, value in overrides.items():
            setattr(report, key, value)
        return report

    def test_a_successful_endpoint_renders_its_findings(self) -> None:
        report = self._report(
            endpoints=[
                EndpointReport(
                    name="Items",
                    path="/api/Items",
                    status=200,
                    content_type="application/json",
                    top_level_type="object",
                    top_level_keys=["data", "totalCount"],
                    record_container_key="data",
                    record_count=3,
                    record_field_names=["itemId", "upc"],
                    record_field_presence={"itemId": 3, "upc": 2},
                    pagination_candidates={"totalCount": 120},
                )
            ]
        )

        rendered = cli.format_report(report)

        assert "status            : 200" in rendered
        assert "record container  : data" in rendered
        assert "itemId" in rendered
        # Sparse fields are flagged, which is what decides NOT NULL later.
        assert "upc  (in 2/3 records)" in rendered
        assert "1/1 endpoints" in rendered

    def test_a_failed_endpoint_renders_its_guidance(self) -> None:
        report = self._report(
            endpoints=[
                EndpointReport(
                    name="PurchaseOrders",
                    path="/api/PurchaseOrders",
                    status=403,
                    error_type="NineyardPermissionError",
                    error_message="/api/PurchaseOrders returned HTTP 403",
                    error_guidance="Record which endpoints the account can read.",
                )
            ]
        )

        rendered = cli.format_report(report)

        assert "FAILED" in rendered
        assert "NineyardPermissionError" in rendered
        assert "Record which endpoints" in rendered

    def test_absent_expiry_fields_are_shown_as_absent_not_invented(self) -> None:
        rendered = cli.format_report(self._report(token_expires_in=None, token_expires=None))

        assert "expiresIn         : (absent)" in rendered
        assert "expires           : (absent)" in rendered

    def test_authentication_failure_renders_guidance_and_stops(self) -> None:
        report = ProbeReport(
            base_url="https://backyard.nineyard.test",
            authenticated=False,
            auth_error="NineyardAuthenticationError: HTTP 401",
            auth_error_guidance="Verify NINEYARD_COMPANY_ID.",
        )

        rendered = cli.format_report(report)

        assert "AUTHENTICATION FAILED" in rendered
        assert "Verify NINEYARD_COMPANY_ID." in rendered


class TestExitCodes:
    def test_all_readable_is_success(self) -> None:
        report = ProbeReport(
            base_url="x",
            authenticated=True,
            endpoints=[EndpointReport(name="Items", path="/api/Items", status=200)],
        )

        assert cli.exit_code_for(report) == cli.EXIT_OK

    def test_authentication_failure_has_its_own_code(self) -> None:
        assert cli.exit_code_for(ProbeReport(base_url="x")) == cli.EXIT_AUTHENTICATION

    def test_an_endpoint_failure_has_its_own_code(self) -> None:
        report = ProbeReport(
            base_url="x",
            authenticated=True,
            endpoints=[EndpointReport(name="Items", path="/api/Items", status=403, error_type="X")],
        )

        assert cli.exit_code_for(report) == cli.EXIT_ENDPOINT_FAILURES


class TestSampleOutput:
    def test_the_report_is_written_as_json(self, tmp_path: Path) -> None:
        report = ProbeReport(
            base_url="https://backyard.nineyard.test",
            authenticated=True,
            token_fingerprint="abc123",
            endpoints=[
                EndpointReport(
                    name="Items",
                    path="/api/Items",
                    status=200,
                    record_field_names=["itemId"],
                    sanitized_sample={"data": [{"itemId": "<int>"}]},
                )
            ],
        )

        destination = cli.save_report(report, tmp_path)
        written = json.loads(destination.read_text(encoding="utf-8"))

        assert destination.parent == tmp_path
        assert written["endpoints"][0]["record_field_names"] == ["itemId"]
        assert written["endpoints"][0]["sanitized_sample"] == {"data": [{"itemId": "<int>"}]}

    def test_the_default_output_directory_is_git_ignored(self) -> None:
        """storage/ is excluded wholesale, so a sample cannot be committed."""
        assert "storage" in cli.DEFAULT_OUTPUT_DIR.parts
        assert "diagnostics" in cli.DEFAULT_OUTPUT_DIR.parts
