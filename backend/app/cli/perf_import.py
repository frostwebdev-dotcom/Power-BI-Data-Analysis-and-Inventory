"""Import a generated 50,000-row vendor file and report wall time and memory
(phase 11 hardening).

    python -m app.cli.perf_import --rows 50000 --format csv
    python -m app.cli.perf_import --rows 50000 --format xlsx

Runs the whole lifecycle in-process against the configured database —
receive → parse → match → snapshot — through the same service functions
the API calls, timing each stage and sampling the process's resident memory
before and after, plus the peak of Python allocations (``tracemalloc``).
The file is built in memory: half the rows carry the seeded demo UPC so the
matcher has hits, the rest are unknown SKUs so the exception queue is
exercised too. Everything it creates is under a vendor whose code starts
with ``PERF``; refused in production.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
import tracemalloc
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import Principal, RoleCode
from app.db.transaction import session_scope
from app.imports.storage import build_storage_backend
from app.models import Organization, User
from app.models.enums import FileFormat
from app.models.vendor import Vendor, VendorImportProfile
from app.schemas.import_profiles import ImportProfileCreate
from app.services import import_profiles, imports
from app.services.import_processing import process_import_job
from app.services.matching import match_import_job
from app.services.snapshots import snapshot_import_job

HEADERS = ["SKU", "UPC", "Description", "Qty", "Cost"]


def rss_bytes() -> int:
    """Peak resident set size of this process, without a dependency."""
    # Dispatch on the name, not sys.platform, so mypy checks both branches.
    readers = {"nt": _rss_windows, "posix": _rss_linux}
    return readers.get(os.name, lambda: 0)()


def _rss_windows() -> int:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    # ``WinDLL`` is intentionally absent from ctypes' public type surface on
    # non-Windows hosts.  Resolve it dynamically so this Windows-only branch
    # remains importable and type-checkable in Linux CI.
    win_dll = getattr(ctypes, "Win" + "DLL")
    kernel32 = win_dll("kernel32", use_last_error=True)
    psapi = win_dll("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        return 0
    return int(counters.PeakWorkingSetSize)


def _rss_linux() -> int:
    try:
        text = Path("/proc/self/status").read_text(encoding="ascii")
    except OSError:
        return 0
    for line in text.splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) * 1024
    return 0


def generate_rows(count: int) -> list[list[str]]:
    rows: list[list[str]] = []
    for i in range(count):
        upc = "012345678905" if i % 2 == 0 else ""
        quantity = str((i * 7) % 50)
        rows.append(
            [f"PERF-{i:06d}", upc, f"Perf item {i}", quantity, f"{(i % 900) / 100 + 1:.2f}"]
        )
    return rows


def build_csv(rows: Sequence[Sequence[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADERS)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def build_xlsx(rows: Sequence[Sequence[str]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Sheet1")
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@dataclass
class Timing:
    stage: str
    seconds: float
    statements: int = 0


class StatementCounter:
    """Counts SQL statements the engine executes, per stage; with
    ``histogram`` it also keeps the most frequent statement heads."""

    def __init__(self, histogram: bool = False) -> None:
        self.count = 0
        self.histogram = histogram
        self.heads: Counter[str] = Counter()

    def install(self) -> None:
        from sqlalchemy import event

        from app.db.session import get_engine

        event.listen(get_engine(), "before_cursor_execute", self._before)

    def _before(
        self, conn: object, cursor: object, statement: str, *_: object, **__: object
    ) -> None:
        self.count += 1
        if self.histogram:
            self.heads[" ".join(statement.split())[:90]] += 1

    def take(self) -> int:
        count, self.count = self.count, 0
        if self.histogram and self.heads:
            for head, n in self.heads.most_common(4):
                print(f"      {n:>7,} x {head}")
            self.heads.clear()
        return count


def ensure_fixture(
    session: Session,
    organization: Organization,
    actor: Principal,
    file_format: FileFormat,
    vendor_code: str | None,
) -> tuple[Vendor, VendorImportProfile]:
    code = vendor_code or f"PERF{file_format.value}"
    vendor = session.execute(
        select(Vendor).where(Vendor.organization_id == organization.id, Vendor.code == code)
    ).scalar_one_or_none()
    if vendor is None:
        vendor = Vendor(
            organization_id=organization.id, code=code, name=f"Perf {file_format.value}"
        )
        session.add(vendor)
        session.flush()
    profiles = import_profiles.list_profiles(session, actor, vendor.id, include_inactive=False)
    if profiles:
        return vendor, profiles[0]
    profile = import_profiles.create_profile(
        session,
        actor,
        vendor.id,
        ImportProfileCreate.model_validate(
            {
                "name": "perf",
                "file_format": file_format.value,
                "column_map": {
                    "columns": [
                        {"target": "vendor_sku", "source": "SKU"},
                        {"target": "upc", "source": "UPC"},
                        {"target": "description", "source": "Description"},
                        {"target": "quantity_available", "source": "Qty"},
                        {"target": "unit_cost", "source": "Cost"},
                    ]
                },
            }
        ),
    )
    return vendor, profile


def run(
    settings: Settings,
    *,
    rows: int,
    file_format: FileFormat,
    org_slug: str,
    trace: bool,
    histogram: bool = False,
    vendor_code: str | None = None,
) -> int:
    storage = build_storage_backend(settings)
    timings: list[Timing] = []
    counter = StatementCounter(histogram=histogram)
    counter.install()
    rss_before = rss_bytes()
    if trace:
        # tracemalloc slows Python two- to four-fold; off by default so the
        # wall time is the real one. --trace-allocations turns it on.
        tracemalloc.start()
    started = time.perf_counter()

    with session_scope() as session:
        organization = session.execute(
            select(Organization).where(Organization.slug == org_slug)
        ).scalar_one_or_none()
        if organization is None:
            print(f"no organization {org_slug!r}; run seed_dev first", file=sys.stderr)
            return 2
        admin = (
            session.execute(
                select(User)
                .where(User.organization_id == organization.id)
                .order_by(User.created_at)
            )
            .scalars()
            .first()
        )
        if admin is None:
            print("no user in the organization; run seed_dev first", file=sys.stderr)
            return 2
        actor = Principal(
            user_id=admin.id,
            organization_id=organization.id,
            email=admin.email,
            display_name=admin.display_name,
            roles=frozenset({RoleCode.ADMIN}),
        )
        vendor, profile = ensure_fixture(session, organization, actor, file_format, vendor_code)

        t0 = time.perf_counter()
        data = generate_rows(rows)
        # A per-run marker keeps every upload byte-distinct (dedupe is by SHA-256).
        data[0][2] = f"Perf item 0 run {uuid.uuid4().hex[:8]}"
        content = build_csv(data) if file_format is FileFormat.CSV else build_xlsx(data)
        del data
        timings.append(Timing("generate file", time.perf_counter() - t0))
        size_bytes = len(content)

        t0 = time.perf_counter()
        outcome = imports.receive_upload(
            session,
            storage,
            principal=actor,
            vendor_id=vendor.id,
            profile_id=profile.id,
            filename=f"perf-{rows}.{file_format.value.lower()}",
            content=content,
            declared_mime=None,
            max_bytes=settings.import_max_upload_mb * 1024 * 1024,
        )
        del content
        timings.append(Timing("receive (store + record)", time.perf_counter() - t0, counter.take()))
        job_id = outcome.job.id

        t0 = time.perf_counter()
        job = process_import_job(
            session, storage, organization_id=organization.id, job_id=job_id, actor=actor
        )
        timings.append(Timing("parse", time.perf_counter() - t0, counter.take()))
        if job.status.value != "RUNNING":
            print(f"parse ended in {job.status.value}: {job.error_message}", file=sys.stderr)
            return 1

        t0 = time.perf_counter()
        match_import_job(session, organization_id=organization.id, job_id=job_id, actor=actor)
        timings.append(Timing("match", time.perf_counter() - t0, counter.take()))

        t0 = time.perf_counter()
        job = snapshot_import_job(
            session, organization_id=organization.id, job_id=job_id, actor=actor
        )
        timings.append(Timing("snapshot", time.perf_counter() - t0, counter.take()))

        total = time.perf_counter() - started
        peak_traced = 0
        if trace:
            _, peak_traced = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        rss_after = rss_bytes()

        print(f"format            {file_format.value}")
        print(f"rows              {rows:,}")
        print(f"file size         {size_bytes / 1024 / 1024:.1f} MB")
        print(f"job               {job_id} -> {job.status.value}")
        print(
            f"counters          total {job.total_rows:,} | matched {job.matched_rows:,} | "
            f"exceptions {job.exception_rows:,} | errors {job.error_rows:,}"
        )
        for timing in timings:
            statements = f"{timing.statements:>9,} statements" if timing.statements else ""
            print(f"{timing.stage:<26}{timing.seconds:8.1f} s  {statements}")
        print(f"{'total wall time':<26}{total:8.1f} s")
        if trace:
            print(f"peak python allocations   {peak_traced / 1024 / 1024:8.1f} MB (tracemalloc)")
        print(
            f"peak resident set         {rss_after / 1024 / 1024:8.1f} MB "
            f"(before run: {rss_before / 1024 / 1024:.1f} MB)"
        )
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    # The report is plain ASCII, but the platform console may not be UTF-8.
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--format", choices=["csv", "xlsx"], default="csv")
    parser.add_argument("--org-slug", default="demo")
    parser.add_argument("--trace-allocations", action="store_true")
    parser.add_argument(
        "--histogram", action="store_true", help="Print the top statements per stage."
    )
    parser.add_argument(
        "--vendor-code",
        default=None,
        help="Vendor code to use or create (default PERF<FORMAT>); new code = first import.",
    )
    arguments = parser.parse_args(argv)
    settings = get_settings()
    if settings.is_production:
        print("perf_import is a development tool", file=sys.stderr)
        return 2
    os.environ.setdefault("PYTHONUTF8", "1")
    return run(
        settings,
        rows=arguments.rows,
        file_format=FileFormat.CSV if arguments.format == "csv" else FileFormat.XLSX,
        org_slug=arguments.org_slug,
        trace=arguments.trace_allocations,
        histogram=arguments.histogram,
        vendor_code=arguments.vendor_code,
    )


if __name__ == "__main__":
    raise SystemExit(main())
