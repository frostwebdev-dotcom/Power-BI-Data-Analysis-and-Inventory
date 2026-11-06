"""Run one configured mailbox poll: python -m app.cli.vendor_mail."""

from app.jobs.vendor_mail import run_vendor_mail_job

if __name__ == "__main__":
    result = run_vendor_mail_job()
    print(f"Vendor inventory email intake: {result}")
    raise SystemExit(0 if result in ("COMPLETED", "BUSY", "NO_ACTIVE_SOURCES", "DISABLED") else 1)
