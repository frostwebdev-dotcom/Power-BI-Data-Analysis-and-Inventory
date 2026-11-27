# 0041. Hold vendor email whose sender did not authenticate

- **Status:** Accepted for implementation; live rollout pending
- **Date:** 2026-11-26
- **Deciders:** Owner-approved fixes from the 26 November 2026 code review
- **Supersedes / Superseded by:** none (refines ADRs 0019/0020)

## Context

Automatic inventory intake routes an email to a vendor source by its `From`
address, subject and file rules (ADRs 0019, 0020), then imports the attached
or linked file without a person looking at it. The `From` header is written
by whoever sends the message. Anyone able to send mail to the intake mailbox
could therefore put a vendor's address in `From`, attach a crafted stock list
and have it imported as that vendor's availability, which feeds the
watchlist, newly-available events and replenishment supplier evidence.

Gmail records what it verified on arrival in an `Authentication-Results`
header (RFC 8601): DMARC for the `From` domain, DKIM signatures and their
signing domains, and SPF for the envelope sender.

## Decision

For the Gmail provider, a routed email is imported automatically only when
Gmail vouches for its `From` domain:

1. Only the **topmost** `Authentication-Results` header counts, and only when
   its authserv-id is `mx.google.com`. Gmail prepends its own; headers further
   down arrived inside the message and may be forged.
2. It passes on `dmarc=pass` for the `From` domain, or on `dkim=pass` whose
   signing domain is the `From` domain or a parent of it. SPF alone does not
   pass: it authenticates the envelope sender, not the `From` address the
   routing rules read.
3. Otherwise each matched source records the delivery as `NEEDS_REVIEW` with
   code `SENDER_NOT_AUTHENTICATED`. No attachment or link is fetched and
   nothing is imported. A staff **retry** of that delivery is the explicit,
   audited override once someone has confirmed the email is genuine.

`VENDOR_MAIL_REQUIRE_SENDER_AUTHENTICATION` (default `true`) turns the check
off for a mailbox whose genuine vendors do not sign their mail. The Microsoft
365 reader does not yet supply this evidence (`sender_authenticated` is
`None`) and its behaviour is unchanged.

## Alternatives considered

| Option | Why not |
|---|---|
| Trust Gmail's spam filtering | Spam filtering is probabilistic and a forged message from a domain without a DMARC reject policy can still reach the inbox. |
| Reject unauthenticated mail outright | A genuine vendor with a misconfigured domain would silently lose updates; review keeps the email visible and recoverable. |
| Require SPF pass | SPF checks the envelope sender, which an attacker controls independently of `From`. |
| Parse every `Authentication-Results` header | Lower headers can be supplied by the sender; only the receiving server's own result is evidence. |

## Consequences

**Positive** — A forged `From` address no longer imports stock under a
vendor's name. The decision and the override are both in the delivery
history and audit log.

**Negative / cost** — A genuine vendor whose mail has neither DMARC alignment
nor an aligned DKIM signature now needs a staff retry per email, or the check
must be turned off for the mailbox.

**Follow-ups** — Before deploying, open one recent genuine email from each
active vendor (for example HAZ) in Gmail's *Show original* view and confirm
DKIM or DMARC shows `PASS`; if not, decide whether to set
`VENDOR_MAIL_REQUIRE_SENDER_AUTHENTICATION=false`. Supply equivalent evidence
from the Microsoft 365 reader if that provider is used.
