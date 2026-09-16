"use client";

import { useState } from "react";

import { EmptyState, ErrorNote, Loading, PageHeader, Pager, StatusBadge, fmtDate, fmtNumber } from "@/components/ui";
import { useEvents, useVendors } from "@/lib/queries";

export default function AvailabilityPage() {
  const [page, setPage] = useState(1);
  const [vendorId, setVendorId] = useState("");
  const [watchlistOnly, setWatchlistOnly] = useState(false);
  const [since, setSince] = useState("");
  const vendors = useVendors({ page_size: 200 });
  const events = useEvents({
    page,
    page_size: 50,
    vendor_id: vendorId,
    watchlist_only: watchlistOnly,
    since: since ? new Date(since).toISOString() : "",
  });
  const vendorCode = (id: string) => vendors.data?.items.find((v) => v.id === id)?.code ?? id.slice(0, 8);

  return (
    <>
      <PageHeader title="Availability" lede="Every time a vendor line changed between unavailable and available, with the two snapshots that show it. Events on watched products are linked to their watch entry." />
      <div className="toolbar">
        <select value={vendorId} onChange={(e) => { setVendorId(e.target.value); setPage(1); }} aria-label="Vendor">
          <option value="">All vendors</option>
          {(vendors.data?.items ?? []).map((v) => (
            <option key={v.id} value={v.id}>
              {v.code} — {v.name}
            </option>
          ))}
        </select>
        <label className="check">
          <input type="checkbox" checked={watchlistOnly} onChange={(e) => { setWatchlistOnly(e.target.checked); setPage(1); }} data-testid="watchlist-only" />
          Watched products only
        </label>
        <label className="check">
          since
          <input type="datetime-local" value={since} onChange={(e) => { setSince(e.target.value); setPage(1); }} aria-label="Since" />
        </label>
      </div>
      <ErrorNote error={events.error} />
      {events.isPending ? <Loading what="Loading events" /> : null}
      {events.data && events.data.items.length === 0 ? <EmptyState title="No availability changes" text="Events appear when an import shows a line flipping between available and unavailable." /> : null}
      {events.data && events.data.items.length > 0 ? (
        <div className="table-wrap">
          <table className="table" data-testid="availability-events">
            <thead>
              <tr>
                <th>Detected</th>
                <th>Event</th>
                <th>Product</th>
                <th>Vendor</th>
                <th>Vendor SKU</th>
                <th>Quantity</th>
                <th>Watched</th>
                <th>Cost ceiling</th>
              </tr>
            </thead>
            <tbody>
              {events.data.items.map((event) => (
                <tr key={event.id} data-testid={`event-${event.event_type}-${event.vendor_sku ?? event.id}`}>
                  <td>{fmtDate(event.detected_at)}</td>
                  <td>
                    <StatusBadge value={event.event_type} />
                  </td>
                  <td>
                    {event.product_name ?? <span className="muted">unmapped line</span>}
                    {event.catalog_item_number ? <span className="mono muted small"> {event.catalog_item_number}</span> : null}
                  </td>
                  <td className="mono">{vendorCode(event.vendor_id)}</td>
                  <td className="mono">{event.vendor_sku ?? "—"}</td>
                  <td>
                    {fmtNumber(event.previous_quantity)} → {fmtNumber(event.new_quantity)}
                  </td>
                  <td>{event.oos_watchlist_id ? <span className="badge badge--accent">watched</span> : "—"}</td>
                  <td>
                    {event.over_max_unit_cost === true ? <span className="badge badge--warn">over ceiling</span> : event.over_max_unit_cost === false ? "within" : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager page={events.data.page} pageSize={events.data.page_size} total={events.data.total} onPage={setPage} />
        </div>
      ) : null}
    </>
  );
}
