"use client";

import Link from "next/link";

import { SystemStatusCard } from "@/components/ApiStatus";
import { ErrorNote, Loading, StatusBadge, fmtDate, fmtNumber } from "@/components/ui";
import { useDashboard } from "@/lib/queries";

/**
 * The dashboard. Every number is a live count from the API; where there is
 * nothing to count yet the card says so instead of showing a zero that looks
 * like data.
 */
export default function DashboardPage() {
  const dashboard = useDashboard();
  const data = dashboard.data;

  return (
    <>
      <header className="page-header">
        <div className="page-header__title-row">
          <h1>Purchasing &amp; Replenishment</h1>
        </div>
        <p className="page-header__lede">
          One catalogue, every vendor&rsquo;s inventory, and a matching process that asks rather
          than guesses.
        </p>
      </header>

      <ErrorNote error={dashboard.error} />
      {dashboard.isPending ? <Loading what="Loading counts" /> : null}

      {data ? (
        <div className="counters counters--tiles" data-testid="dashboard-counts">
          <Link href="/exceptions" className="counter counter--tile">
            <span className="counter__value" data-testid="count-open-exceptions">
              {fmtNumber(data.open_exceptions)}
            </span>
            <span className="counter__label">open exceptions</span>
          </Link>
          <Link href="/availability?watchlist=1" className="counter counter--tile">
            <span className="counter__value" data-testid="count-newly-available">
              {fmtNumber(data.watchlist_newly_available_7d)}
            </span>
            <span className="counter__label">watched items back in stock, 7 days</span>
          </Link>
          <Link href="/watchlist" className="counter counter--tile">
            <span className="counter__value">
              {fmtNumber(data.watchlist_in_stock)} / {fmtNumber(data.watchlist_active)}
            </span>
            <span className="counter__label">watched items in stock / watched</span>
          </Link>
          <Link href="/imports" className="counter counter--tile">
            <span className="counter__value">{fmtNumber(data.imports_running)}</span>
            <span className="counter__label">imports running</span>
          </Link>
        </div>
      ) : null}

      <div className="grid grid--2" style={{ marginTop: 26 }}>
        <section className="card">
          <div className="card__header">
            <span className="card__title">Last import per vendor</span>
          </div>
          {data && data.last_imports.length === 0 ? <p className="muted">No vendors yet.</p> : null}
          {data && data.last_imports.length > 0 ? (
            <table className="table table--compact" data-testid="last-imports">
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Status</th>
                  <th>Rows</th>
                  <th>Exceptions</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {data.last_imports.map((row) => (
                  <tr key={row.vendor_id}>
                    <td>
                      <span className="mono">{row.vendor_code}</span> {row.vendor_name}
                    </td>
                    <td>{row.job_id ? <Link href={`/imports?job=${row.job_id}`}><StatusBadge value={row.status} /></Link> : <span className="muted">never imported</span>}</td>
                    <td>{fmtNumber(row.total_rows)}</td>
                    <td>{fmtNumber(row.exception_rows)}</td>
                    <td>{fmtDate(row.completed_at ?? row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </section>

        <section className="card">
          <div className="card__header">
            <span className="card__title">Synchronisations</span>
          </div>
          <table className="table table--compact">
            <thead>
              <tr>
                <th>Source</th>
                <th>Status</th>
                <th>Started</th>
                <th>Completed</th>
              </tr>
            </thead>
            <tbody>
              {data?.amazon_syncs.map((sync) => (
                <tr key={sync.job_type}>
                  <td>Amazon · {sync.job_type?.toLowerCase()}</td>
                  <td>
                    <StatusBadge value={sync.status} />
                  </td>
                  <td>{fmtDate(sync.started_at)}</td>
                  <td>{fmtDate(sync.completed_at)}</td>
                </tr>
              ))}
              {data && data.amazon_syncs.length === 0 ? (
                <tr>
                  <td>Amazon</td>
                  <td colSpan={3} className="muted">
                    never run
                  </td>
                </tr>
              ) : null}
              <tr>
                <td>Nineyard catalogue</td>
                {data?.nineyard_sync ? (
                  <>
                    <td>
                      <StatusBadge value={data.nineyard_sync.status} />
                    </td>
                    <td>{fmtDate(data.nineyard_sync.started_at)}</td>
                    <td>{fmtDate(data.nineyard_sync.completed_at)}</td>
                  </>
                ) : (
                  <td colSpan={3} className="muted">
                    not yet connected (B1)
                  </td>
                )}
              </tr>
            </tbody>
          </table>
        </section>

        <SystemStatusCard />

        <section className="card">
          <div className="card__header">
            <span className="card__title">Matching policy</span>
          </div>
          <div className="kv">
            {[
              ["1", "Normalised UPC", "Automatic"],
              ["2", "Catalogue item number", "Automatic"],
              ["3", "Approved vendor SKU", "Automatic"],
              ["4", "Approved marketplace SKU", "Automatic"],
              ["5", "Suggested match", "Needs approval"],
            ].map(([n, rule, how]) => (
              <div className="kv__row" key={n}>
                <span className="kv__key">
                  {n} &nbsp;{rule}
                </span>
                <span className="kv__value" style={how === "Needs approval" ? { color: "var(--warn)" } : undefined}>
                  {how}
                </span>
              </div>
            ))}
          </div>
          <p className="card__note">
            A description alone never matches a product automatically. Anything ambiguous goes to
            the exception queue rather than being resolved by guesswork.
          </p>
        </section>
      </div>
    </>
  );
}
