"""Local-only Gmail consent bootstrap. Never run its callback in an Azure console."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TextIO

from app.core.config import Settings, get_settings
from app.integrations.vendor_mail.gmail_oauth import CALLBACK_PATH, GmailAuthorization
from app.integrations.vendor_mail.types import IntakeError


def receive_authorization(
    settings: Settings,
    *,
    port: int = 8765,
    seconds: float = 180,
    open_browser: Callable[[str], bool] = webbrowser.open,
) -> tuple[GmailAuthorization, str]:
    code: str | None = None
    failure: IntakeError | None = None
    requests = 0
    flow: GmailAuthorization

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            self.request.settimeout(5)
            super().setup()

        def log_message(self, format: str, *args: object) -> None:
            # The ordinary HTTP access log would contain an authorization code.
            pass

        def do_GET(self) -> None:
            nonlocal code, failure, requests
            requests += 1
            status = 200
            try:
                code = flow.accept_callback(self.path, self.headers.get("Host", ""))
            except IntakeError as error:
                if error.code == "GMAIL_AUTHORIZATION_CANCELLED":
                    failure = error
                else:
                    status = 400
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with suppress(OSError):
                self.wfile.write(b"Return to the setup terminal. You may close this window.")

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        raise IntakeError("GMAIL_CALLBACK_PORT_UNAVAILABLE") from None
    with server:
        redirect_uri = f"http://127.0.0.1:{server.server_port}{CALLBACK_PATH}"
        flow = GmailAuthorization(settings, redirect_uri)
        server.timeout = min(1.0, seconds)
        if not open_browser(flow.authorization_url()):
            raise IntakeError("GMAIL_LOCAL_BROWSER_REQUIRED")
        deadline = time.monotonic() + seconds
        while code is None and failure is None:
            if time.monotonic() >= deadline:
                raise IntakeError("GMAIL_AUTHORIZATION_TIMED_OUT")
            if requests >= 32:
                raise IntakeError("GMAIL_CALLBACK_INVALID")
            server.handle_request()
        if failure:
            raise failure
        assert code is not None
        return flow, code


def _protect_output(path: Path, descriptor: int) -> None:
    if os.name != "nt":
        os.fchmod(descriptor, 0o600)
        return
    # Restrict the empty NTFS file before any credential is written. chmod alone
    # on Windows does not establish an owner-only ACL.
    try:
        account = subprocess.run(
            ["whoami"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if not account or "\n" in account or "\r" in account:
            raise ValueError("invalid local account")
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(F)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        raise IntakeError("GMAIL_OUTPUT_PERMISSION_FAILED") from None


@contextmanager
def private_output(path: Path) -> Iterator[TextIO]:
    path = path.expanduser().absolute()
    if any((parent / ".git").exists() for parent in path.parents):
        raise IntakeError("GMAIL_OUTPUT_MUST_BE_OUTSIDE_GIT")
    try:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        parent = path.parent.resolve(strict=True)
        path = parent / path.name
        if any((ancestor / ".git").exists() for ancestor in path.parents):
            raise IntakeError("GMAIL_OUTPUT_MUST_BE_OUTSIDE_GIT")
        if os.name != "nt":
            metadata = parent.stat()
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise IntakeError("GMAIL_OUTPUT_DIRECTORY_NOT_PRIVATE")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise IntakeError("GMAIL_OUTPUT_ALREADY_EXISTS") from None
    except OSError:
        raise IntakeError("GMAIL_OUTPUT_UNAVAILABLE") from None
    inode = os.fstat(descriptor).st_ino
    try:
        _protect_output(path, descriptor)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = -1
            yield output
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            if path.stat().st_ino == inode:
                path.unlink()  # only this helper's new, incomplete output
        except OSError:
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authorize the inventory Gmail mailbox locally.")
    parser.add_argument(
        "--output", type=Path, required=True, help="New private JSON file outside Git."
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or not 30 <= args.timeout <= 600:
        parser.error("port must be 1..65535 and timeout 30..600 seconds")
    try:
        settings = get_settings()
        print("Starting read-only inventory-mailbox authorization.")
        with private_output(args.output) as output:
            flow, code = receive_authorization(settings, port=args.port, seconds=args.timeout)
            bundle = flow.exchange(code)
            json.dump(bundle, output, ensure_ascii=True, indent=2)
            output.write("\n")
        print(
            "Verified the mailbox. Runtime settings saved privately; automatic imports remain off."
        )
        return 0
    except IntakeError as error:
        print(f"Gmail setup stopped: {error.code}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Gmail setup cancelled.", file=sys.stderr)
        return 2
    except Exception:
        print("Gmail setup stopped: GMAIL_SETUP_FAILED", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
