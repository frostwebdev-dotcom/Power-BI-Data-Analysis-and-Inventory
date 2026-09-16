"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Drawer, EmptyState, ErrorNote, JsonBlock, KeyValues, Loading, Note, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useException, useExceptions, useVendors } from "@/lib/queries";
import type { MappingExceptionDetail, Resolution } from "@/lib/types";

const REASONS = ["NO_MATCH", "AMBIGUOUS_MATCH", "SUGGESTION_ONLY", "CONFLICTING_IDENTIFIER"];

export default function ExceptionsPage() {
  const { hasRole } = useAuth();
  const canDecide = hasRole("PURCHASING_MANAGER");
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("PENDING");
  const [reason, setReason] = useState("");
  const [vendorId, setVendorId] = useState("");
  const [includeDeferred, setIncludeDeferred] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const vendors = useVendors({ page_size: 200 });
  const queue = useExceptions({ page, page_size: 25, status, reason, vendor_id: vendorId, include_deferred: includeDeferred });

  return (
    <>
      <PageHeader title="Exception queue" lede="Rows the matcher could not settle on its own. Nothing here was guessed: it found nothing, found more than one, or could only suggest — so it asks." />
      <div className="toolbar">
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Status">
          <option value="PENDING">Pending</option>
          <option value="APPROVED">Approved</option>
          <option value="REJECTED">Rejected</option>
        </select>
        <select value={reason} onChange={(e) => { setReason(e.target.value); setPage(1); }} aria-label="Reason">
          <option value="">Any reason</option>
          {REASONS.map((r) => (
            <option key={r} value={r}>
              {r.replaceAll("_", " ").toLowerCase()}
            </option>
          ))}
        </select>
        <select value={vendorId} onChange={(e) => { setVendorId(e.target.value); setPage(1); }} aria-label="Vendor">
          <option value="">All vendors</option>
          {(vendors.data?.items ?? []).map((v) => (
            <option key={v.id} value={v.id}>
              {v.code} — {v.name}
            </option>
          ))}
        </select>
        <label className="check">
          <input type="checkbox" checked={includeDeferred} onChange={(e) => setIncludeDeferred(e.target.checked)} />
          Show deferred
        </label>
      </div>
      <ErrorNote error={queue.error} />
      {queue.isPending ? <Loading what="Loading the queue" /> : null}
      {queue.data && queue.data.items.length === 0 ? <EmptyState title="Nothing waiting" text="Every row of every import has been settled." /> : null}
      {queue.data && queue.data.items.length > 0 ? (
        <div className="table-wrap">
          <table className="table" data-testid="exception-queue">
            <thead>
              <tr>
                <th>Vendor SKU</th>
                <th>Description</th>
                <th>Reason</th>
                <th>Status</th>
                <th>Row</th>
                <th>Age</th>
                <th>Deferred until</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {queue.data.items.map((item) => (
                <tr key={item.id} data-testid={`exception-row-${item.vendor_sku ?? item.id}`}>
                  <td className="mono">{item.vendor_sku ?? "—"}</td>
                  <td>{item.description ?? "—"}</td>
                  <td>
                    <StatusBadge value={item.reason} />
                  </td>
                  <td>
                    <StatusBadge value={item.status} />
                  </td>
                  <td>{item.row_number ?? "—"}</td>
                  <td>{item.age_hours !== null ? `${item.age_hours.toFixed(1)} h` : "—"}</td>
                  <td>{fmtDate(item.deferred_until)}</td>
                  <td className="actions">
                    <button type="button" className="button button--ghost" onClick={() => setSelected(item.id)}>
                      {canDecide && item.status === "PENDING" ? "Review" : "Open"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager page={queue.data.page} pageSize={queue.data.page_size} total={queue.data.total} onPage={setPage} />
        </div>
      ) : null}
      {selected ? <ExceptionDrawer id={selected} canDecide={canDecide} onClose={() => setSelected(null)} /> : null}
    </>
  );
}

function ExceptionDrawer({ id, canDecide, onClose }: { id: string; canDecide: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const detail = useException(id);
  const [productId, setProductId] = useState("");
  const [note, setNote] = useState("");
  const [supersede, setSupersede] = useState(false);
  const [deferUntil, setDeferUntil] = useState("");
  const [outcome, setOutcome] = useState<string | null>(null);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["exceptions"] });
    await queryClient.invalidateQueries({ queryKey: ["imports"] });
    await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const approve = useMutation({
    mutationFn: (chosen: string) =>
      api.post<Resolution>(`/exceptions/${id}/approve`, { product_id: chosen, supersede, note: note || null }),
    onSuccess: async (result) => {
      setOutcome(
        result.superseded_product_id
          ? `Approved; the previous mapping (${result.superseded_product_id.slice(0, 8)}…) was superseded.`
          : "Approved. This vendor SKU now maps to the product on every later import.",
      );
      await refresh();
    },
  });
  const reject = useMutation({
    mutationFn: () => api.post<Resolution>(`/exceptions/${id}/reject`, { note }),
    onSuccess: async () => {
      setOutcome("Rejected. No mapping was made.");
      await refresh();
    },
  });
  const defer = useMutation({
    mutationFn: () => api.post<Resolution>(`/exceptions/${id}/defer`, { until: new Date(deferUntil).toISOString(), note: note || null }),
    onSuccess: async () => {
      setOutcome("Deferred; it leaves the queue until then.");
      await refresh();
    },
  });

  if (detail.isPending) {
    return (
      <Drawer title="Exception" onClose={onClose} wide>
        <Loading />
      </Drawer>
    );
  }
  if (detail.error || !detail.data) {
    return (
      <Drawer title="Exception" onClose={onClose} wide>
        <ErrorNote error={detail.error ?? new Error("Not found")} />
      </Drawer>
    );
  }
  const item: MappingExceptionDetail = detail.data;
  const decidable = canDecide && item.status === "PENDING";
  const needsSupersede = approve.error instanceof ApiError && approve.error.code === "mapping_supersession_required";

  return (
    <Drawer title={`${item.vendor_sku ?? "Row"} — ${item.reason.replaceAll("_", " ").toLowerCase()}`} onClose={onClose} wide>
      <div className="grid grid--2">
        <section className="card">
          <h3 className="card__title">The row as imported</h3>
          {item.source_row ? (
            <>
              <KeyValues rows={Object.entries(item.source_row.raw_data).map(([k, v]) => [k, <span key={k} className="mono">{v}</span>])} />
              <p className="muted small">
                Row {item.source_row.row_number} of{" "}
                {item.import_job_id ? <Link href={`/imports?job=${item.import_job_id}`}>this import</Link> : "an import"} · normalised UPC{" "}
                <span className="mono">{item.source_row.normalized_upc ?? "—"}</span>
              </p>
            </>
          ) : (
            <p className="muted">No source row (a listing or a line without an import row).</p>
          )}
          {item.vendor_line ? (
            <KeyValues
              rows={[
                ["Vendor line", <span key="l" className="mono">{item.vendor_line.vendor_sku}</span>],
                ["Mapping", <StatusBadge key="m" value={item.vendor_line.mapping_status} />],
                ["Mapped product", item.vendor_line.product_id ? <span key="p" className="mono">{item.vendor_line.product_id}</span> : "—"],
              ]}
            />
          ) : null}
        </section>
        <section className="card">
          <h3 className="card__title">Candidates</h3>
          {item.candidates.length === 0 ? <p className="muted">The chain found nothing.</p> : null}
          {item.candidates.map((candidate) => (
            <div className="candidate" key={`${candidate.product_id}-${candidate.rule}`} data-testid="candidate">
              <div>
                <strong>{candidate.product?.name ?? "Unknown product"}</strong>{" "}
                <span className="mono muted">{candidate.product?.catalog_item_number ?? candidate.product_id.slice(0, 8)}</span>
                <div className="small muted">
                  {candidate.rule.replaceAll("_", " ").toLowerCase()} (priority {candidate.priority})
                  {candidate.identifier ? ` · ${candidate.identifier}` : ""}
                  {candidate.score !== null ? ` · similarity ${(candidate.score * 100).toFixed(0)}%` : ""}
                </div>
              </div>
              {decidable ? (
                <button type="button" className="button button--primary" disabled={approve.isPending} onClick={() => approve.mutate(candidate.product_id)} data-testid="approve-candidate">
                  Approve
                </button>
              ) : null}
            </div>
          ))}
          {decidable ? (
            <div className="field">
              <span className="field__label">Or another product (id)</span>
              <div className="toolbar">
                <input value={productId} onChange={(e) => setProductId(e.target.value)} placeholder="product UUID" className="mono" />
                <button type="button" className="button" disabled={!productId || approve.isPending} onClick={() => approve.mutate(productId.trim())}>
                  Approve
                </button>
              </div>
              <span className="field__hint">Product search by catalog number, UPC or name arrives with the products screen (B9).</span>
            </div>
          ) : null}
        </section>
      </div>

      <section className="card">
        <h3 className="card__title">Rule trail</h3>
        <table className="table table--compact">
          <thead>
            <tr>
              <th>Priority</th>
              <th>Rule</th>
              <th>Input</th>
              <th>Outcome</th>
              <th>Candidates</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {(item.match_evaluations.rules ?? []).map((rule) => (
              <tr key={`${rule.priority}-${rule.rule}`}>
                <td>{rule.priority}</td>
                <td>{rule.rule.replaceAll("_", " ").toLowerCase()}</td>
                <td className="mono">{rule.input ?? "—"}</td>
                <td>
                  <StatusBadge value={rule.outcome === "matched" ? "OK" : rule.outcome === "ambiguous" ? "WARNING" : rule.outcome.toUpperCase()} />
                </td>
                <td>{rule.candidates.length}</td>
                <td className="muted small">{rule.note ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {decidable ? (
        <section className="card">
          <h3 className="card__title">Decision</h3>
          <label className="field">
            <span className="field__label">Note</span>
            <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why — required to reject" data-testid="decision-note" />
          </label>
          {needsSupersede ? (
            <Note tone="warn">
              This vendor SKU already has an approved mapping to a different product. Tick to supersede it; both steps are recorded in the audit log.
              <label className="check">
                <input type="checkbox" checked={supersede} onChange={(e) => setSupersede(e.target.checked)} />
                Supersede the existing mapping
              </label>
            </Note>
          ) : null}
          <div className="toolbar">
            <button type="button" className="button button--danger" disabled={!note.trim() || reject.isPending} onClick={() => reject.mutate()} data-testid="reject">
              Reject
            </button>
            <input type="datetime-local" value={deferUntil} onChange={(e) => setDeferUntil(e.target.value)} aria-label="Defer until" />
            <button type="button" className="button" disabled={!deferUntil || defer.isPending} onClick={() => defer.mutate()}>
              Defer
            </button>
          </div>
          <ErrorNote error={needsSupersede ? null : (approve.error ?? reject.error ?? defer.error)} />
        </section>
      ) : null}
      {outcome ? (
        <Note tone="ok">
          <span data-testid="decision-outcome">{outcome}</span>
        </Note>
      ) : null}
      {item.status !== "PENDING" ? (
        <Note>
          {item.status.toLowerCase()} {fmtDate(item.resolved_at)}
          {item.resolved_product ? ` → ${item.resolved_product.name} (${item.resolved_product.catalog_item_number})` : ""}
          {item.resolution_note ? ` — ${item.resolution_note}` : ""}
          {item.resolved_product_id ? (
            <>
              {" · "}
              <Link href={`/watchlist?product_id=${item.resolved_product_id}`} data-testid="watch-this-product">
                Watch this product
              </Link>
            </>
          ) : null}
        </Note>
      ) : null}
      <details>
        <summary className="muted small">Raw evaluation JSON</summary>
        <JsonBlock value={item.match_evaluations} />
      </details>
    </Drawer>
  );
}
