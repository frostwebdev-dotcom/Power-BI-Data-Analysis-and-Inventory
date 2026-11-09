"""One read-only, incremental vendor mailbox poll per configured deployment."""

from __future__ import annotations

import uuid

from app.core.config import Settings, get_settings
from app.core.context import set_request_id
from app.core.logging import get_logger
from app.db.transaction import session_lifecycle
from app.imports.storage import build_storage_backend
from app.integrations.vendor_mail.gmail import GmailMailReader
from app.integrations.vendor_mail.graph import GraphMailReader
from app.integrations.vendor_mail.types import IntakeError
from app.jobs.runner import JobRunner
from app.services.vendor_mail import configured_organization, run_intake


def run_vendor_mail_job(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if not settings.vendor_mail_enabled:
        return "DISABLED"
    set_request_id("vendor-mail-" + str(uuid.uuid4()))
    logger = get_logger(__name__)
    try:
        with session_lifecycle() as session:
            organization_id = configured_organization(session, settings)
            mail_reader = (
                GmailMailReader(settings)
                if settings.vendor_mail_provider == "gmail"
                else GraphMailReader(settings)
            )
            with mail_reader as reader:
                result = run_intake(
                    session, organization_id, settings, reader, build_storage_backend(settings)
                )
        logger.info("vendor_mail.poll.finished", outcome=result)
        return result
    except IntakeError as error:
        logger.warning("vendor_mail.poll.refused", error_code=error.code)
        return error.code
    except Exception as error:
        logger.error("vendor_mail.poll.failed", exception_type=type(error).__name__)
        return "POLL_INTERNAL_ERROR"


def register_vendor_mail_jobs(runner: JobRunner, settings: Settings) -> None:
    def poll() -> None:
        run_vendor_mail_job(settings)

    runner.schedule(
        "vendor.inventory.email",
        poll,
        interval_minutes=settings.vendor_mail_poll_minutes,
    )
