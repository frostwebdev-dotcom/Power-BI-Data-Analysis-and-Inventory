"use client";

/**
 * Small, shared presentational pieces. No data fetching here.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import { ApiError } from "@/lib/api";

export function PageHeader({
  title,
  lede,
  actions,
}: {
  title: string;
  lede?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header__title-row">
        <h1>{title}</h1>
        {actions ? <div className="page-header__actions">{actions}</div> : null}
      </div>
      {lede ? <p className="page-header__lede">{lede}</p> : null}
    </header>
  );
}

const TONES: Record<string, string> = {
  OK: "badge--ok",
  COMPLETED: "badge--ok",
  MATCHED: "badge--ok",
  APPROVED: "badge--ok",
  AVAILABLE: "badge--ok",
  IN_STOCK: "badge--ok",
  BECAME_AVAILABLE: "badge--ok",
  ACTIVE: "badge--ok",
  SUCCEEDED: "badge--ok",
  WARNING: "badge--warn",
  COMPLETED_WITH_ERRORS: "badge--warn",
  PENDING: "badge--warn",
  RUNNING: "badge--accent",
  SUGGESTION_ONLY: "badge--warn",
  AMBIGUOUS: "badge--warn",
  UNKNOWN: "badge--warn",
  ERROR: "badge--danger",
  FAILED: "badge--danger",
  REJECTED: "badge--danger",
  UNMATCHED: "badge--danger",
  OUT_OF_STOCK: "badge--danger",
  BECAME_UNAVAILABLE: "badge--danger",
  CONFLICTING_IDENTIFIER: "badge--danger",
  SKIPPED: "",
};

export function StatusBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  const tone = TONES[value] ?? "";
  return <span className={`badge ${tone}`}>{value.replaceAll("_", " ").toLowerCase()}</span>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message =
    error instanceof ApiError
      ? `${error.message} (${error.code})`
      : error instanceof Error
        ? error.message
        : String(error);
  return (
    <div className="note note--danger" role="alert">
      {message}
    </div>
  );
}

export function Note({ tone = "info", children }: { tone?: "info" | "ok" | "warn"; children: ReactNode }) {
  return <div className={`note note--${tone}`}>{children}</div>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="muted" role="status">
      {what}…
    </p>
  );
}

export function EmptyState({ title, text }: { title: string; text?: string }) {
  return (
    <div className="empty empty--compact">
      <div className="empty__title">{title}</div>
      {text ? <p className="empty__text">{text}</p> : null}
    </div>
  );
}

export function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <div className="pager">
      <button type="button" className="button button--ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Previous
      </button>
      <span className="muted">
        Page {page} of {pages} · {total.toLocaleString()} total
      </span>
      <button type="button" className="button button--ghost" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next
      </button>
    </div>
  );
}

/**
 * Timestamps arrive as UTC and are shown in the reader's own zone, which is
 * only honest if the zone is named (AC-13.6). `dateStyle`/`timeStyle` cannot be
 * combined with `timeZoneName`, so the components are spelled out.
 */
const DATE_TIME: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  timeZoneName: "short",
};

export function fmtDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, DATE_TIME);
}

/**
 * The same moment, short enough for a narrow card: no seconds, and the year
 * only when it is not the current one.
 */
export function fmtWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

/** How long something took, from two timestamps. Null when it cannot be known. */
export function fmtDuration(
  from: string | null | undefined,
  to: string | null | undefined,
): string | null {
  if (!from || !to) return null;
  const seconds = (new Date(to).getTime() - new Date(from).getTime()) / 1000;
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function fmtNumber(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const number = typeof value === "number" ? value : Number(value);
  return Number.isNaN(number) ? String(value) : number.toLocaleString();
}

export function Drawer({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="drawer-backdrop" onClick={onClose} role="presentation">
      <aside
        className={`drawer${wide ? " drawer--wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="drawer__header">
          <h2>{title}</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        <div className="drawer__body">{children}</div>
      </aside>
    </div>
  );
}

export function KeyValues({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([key, value]) => (
        <div className="kv__row" key={key}>
          <dt className="kv__key">{key}</dt>
          <dd className="kv__value">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

export function IdLink({ href, id }: { href: string; id: string }) {
  return (
    <Link href={href} className="mono">
      {id.slice(0, 8)}…
    </Link>
  );
}
