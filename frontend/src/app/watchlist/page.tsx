"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import type { FormEvent } from "react";

import { Drawer, EmptyState, ErrorNote, KeyValues, Loading, PageHeader, Pager, StatusBadge, fmtDate, fmtNumber } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useVendors, useWatchHistory, useWatchlist } from "@/lib/queries";
import type { WatchlistEntry } from "@/lib/types";

export default function WatchlistPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Watchlist />
    </Suspense>
  );
}

function Watchlist() {
  const params = useSearchParams();
  const { hasRole } = useAuth();
  const canWrite = hasRole("PURCHASING_MANAGER");
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [vendorId, setVendorId] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [adding, setAdding] = useState(params.get("product_id") !== null);
  const [history, setHistory] = useState<string | null>(null);
  const vendors = useVendors({ page_size: 200 });
  const entries = useWatchlist({ page, page_size: 25, vendor_id: vendorId, include_inactive: includeInactive });

  const remove = useMutation({
    mutationFn: (id: string) => api.post<WatchlistEntry>(`/watchlist/${id}/remove`, {}),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["watchlist"] });
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
  const vendorCode = (id: string | null) => (id ? (vendors.data?.items.find((v) => v.id === id)?.code ?? id.slice(0, 8)) : "any vendor");

  return (
    <>
      <PageHeader
        title="Watchlist"
        lede="Products you want to buy when they come back. When any import shows one available, the event is linked here and the status flips."
        actions={
          canWrite ? (
            <button type="button" className="button button--primary" onClick={() => setAdding(true)} data-testid="watch-add">
              Watch a product
            </button>
          ) : null
        }
      />
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
          <input type="checkbox" checked={includeInactive} onChange={(e) => setIncludeInactive(e.target.checked)} />
          Show removed
        </label>
      </div>
      <ErrorNote error={entries.error ?? remove.error} />
      {entries.isPending ? <Loading what="Loading the watchlist" /> : null}
      {entries.data && entries.data.items.length === 0 ? <EmptyState title="Nothing watched" /> : null}
      {entries.data && entries.data.items.length > 0 ? (
        <div className="table-wrap">
          <table className="table" data-testid="watchlist">
            <thead>
              <tr>
                <th>Product</th>
                <th>Catalog #</th>
                <th>Vendor</th>
                <th>Status</th>
                <th>Priority</th>
                <th>Want</th>
                <th>Max cost</th>
                <th>Changed</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {entries.data.items.map((entry) => (
                <tr key={entry.id} data-testid={`watch-row-${entry.product.catalog_item_number}`}>
                  <td>{entry.product.name}</td>
                  <td className="mono">{entry.product.catalog_item_number}</td>
                  <td>{vendorCode(entry.vendor_id)}</td>
                  <td>
                    <StatusBadge value={entry.is_active ? entry.current_status : "INACTIVE"} />
                  </td>
                  <td>{entry.priority.toLowerCase()}</td>
                  <td>{fmtNumber(entry.desired_quantity)}</td>
                  <td>{fmtNumber(entry.max_unit_cost)}</td>
                  <td>{fmtDate(entry.current_status_changed_at)}</td>
                  <td className="actions">
                    <button type="button" className="button button--ghost" onClick={() => setHistory(entry.id)}>
                      History
                    </button>
                    {canWrite && entry.is_active ? (
                      <button type="button" className="button button--ghost" onClick={() => remove.mutate(entry.id)}>
                        Remove
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager page={entries.data.page} pageSize={entries.data.page_size} total={entries.data.total} onPage={setPage} />
        </div>
      ) : null}
      {adding ? <AddWatch initialProductId={params.get("product_id") ?? ""} onClose={() => setAdding(false)} /> : null}
      {history ? <HistoryDrawer id={history} onClose={() => setHistory(null)} /> : null}
    </>
  );
}

function AddWatch({ initialProductId, onClose }: { initialProductId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const vendors = useVendors({ page_size: 200 });
  const [form, setForm] = useState({
    product_id: initialProductId,
    vendor_id: "",
    reason: "",
    priority: "NORMAL",
    desired_quantity: "",
    max_unit_cost: "",
  });
  const add = useMutation({
    mutationFn: () =>
      api.post<WatchlistEntry>("/watchlist", {
        product_id: form.product_id.trim(),
        vendor_id: form.vendor_id || null,
        reason: form.reason || null,
        priority: form.priority,
        desired_quantity: form.desired_quantity || null,
        max_unit_cost: form.max_unit_cost || null,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["watchlist"] });
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      onClose();
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    add.mutate();
  }
  return (
    <Drawer title="Watch a product" onClose={onClose}>
      <form className="form" onSubmit={submit} data-testid="watch-form">
        <label className="field">
          <span className="field__label">Product id</span>
          <input name="product_id" required className="mono" value={form.product_id} onChange={(e) => setForm({ ...form, product_id: e.target.value })} />
          <span className="field__hint">From an exception&apos;s approved product or the catalogue. Search by name arrives with the products screen (B9).</span>
        </label>
        <label className="field">
          <span className="field__label">Vendor</span>
          <select name="vendor_id" value={form.vendor_id} onChange={(e) => setForm({ ...form, vendor_id: e.target.value })}>
            <option value="">Any vendor</option>
            {(vendors.data?.items ?? []).map((v) => (
              <option key={v.id} value={v.id}>
                {v.code} — {v.name}
              </option>
            ))}
          </select>
        </label>
        <div className="grid grid--3">
          <label className="field">
            <span className="field__label">Priority</span>
            <select name="priority" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
              {["LOW", "NORMAL", "HIGH", "CRITICAL"].map((p) => (
                <option key={p} value={p}>
                  {p.toLowerCase()}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Desired quantity</span>
            <input name="desired_quantity" inputMode="decimal" value={form.desired_quantity} onChange={(e) => setForm({ ...form, desired_quantity: e.target.value })} />
          </label>
          <label className="field">
            <span className="field__label">Max unit cost</span>
            <input name="max_unit_cost" inputMode="decimal" value={form.max_unit_cost} onChange={(e) => setForm({ ...form, max_unit_cost: e.target.value })} />
          </label>
        </div>
        <label className="field">
          <span className="field__label">Reason</span>
          <input name="reason" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
        </label>
        <ErrorNote error={add.error} />
        <div className="form__actions">
          <button type="submit" className="button button--primary" disabled={add.isPending} data-testid="watch-submit">
            Add to watchlist
          </button>
        </div>
      </form>
    </Drawer>
  );
}

function HistoryDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const history = useWatchHistory(id);
  return (
    <Drawer title="Status history" onClose={onClose}>
      <ErrorNote error={history.error} />
      {history.data ? (
        <>
          <KeyValues
            rows={[
              ["Product", history.data.entry.product.name],
              ["Status", <StatusBadge key="s" value={history.data.entry.current_status} />],
              ["Added", fmtDate(history.data.entry.added_at)],
              ["Reason", history.data.entry.reason ?? "—"],
            ]}
          />
          <table className="table table--compact" data-testid="watch-history">
            <thead>
              <tr>
                <th>When</th>
                <th>From</th>
                <th>To</th>
              </tr>
            </thead>
            <tbody>
              {history.data.history.map((h) => (
                <tr key={h.id}>
                  <td>{fmtDate(h.changed_at)}</td>
                  <td>
                    <StatusBadge value={h.previous_status} />
                  </td>
                  <td>
                    <StatusBadge value={h.new_status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {history.data.history.length === 0 ? <p className="muted">No status change yet.</p> : null}
        </>
      ) : (
        <Loading />
      )}
    </Drawer>
  );
}
