"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";

import { EmptyState, ErrorNote, KeyValues, Loading, Note, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { financialError, financialReason, useFinancialBindingHistory } from "@/lib/financial-imports";
import type { FinancialBindingEvidence, FinancialBindingPreview, FinancialBindingRevision } from "@/lib/financial-imports";

export function FinancialBindings({ identifier, selectedId, onSelect }: { identifier: string; selectedId: string; onSelect: (id: string) => void }) {
  const { hasRole } = useAuth();
  const cache = useQueryClient();
  const [page, setPage] = useState(1);
  const history = useFinancialBindingHistory(identifier, page);
  const [selection, setSelection] = useState<FinancialBindingRevision | null>(null);
  const [preview, setPreview] = useState<FinancialBindingPreview | null>(null);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const path = "/financial-imports/" + encodeURIComponent(identifier) + "/binding-revisions";
  const propose = useMutation({
    mutationFn: async (nextPage: number) => {
      try { return await api.post<FinancialBindingPreview>(path + "/preview?page=" + nextPage + "&page_size=25"); }
      catch (error) { throw financialError(error); }
    },
    onMutate: () => { setPreview(null); setConfirmed(false); setReason(""); },
    onSuccess: setPreview,
  });
  const record = useMutation({
    mutationFn: async (proposal: FinancialBindingPreview) => {
      try { return await api.post<FinancialBindingRevision>(path, { expected_revision_id: proposal.expected_revision_id, bindings_sha256: proposal.bindings_sha256, reason: reason.trim() }); }
      catch (error) { throw financialError(error); }
    },
    onSuccess: (revision) => {
      setPreview(null); setReason(""); setConfirmed(false); setPage(1);
      setSelection(revision); onSelect(revision.id);
      void cache.invalidateQueries({ queryKey: ["financial-imports", identifier] });
    },
    onError: () => { setPreview(null); setConfirmed(false); },
  });
  const busy = propose.isPending || record.isPending;
  function choose(revision: FinancialBindingRevision | null) { setSelection(revision); onSelect(revision?.id ?? ""); }
  function runPreview(nextPage: number) { record.reset(); propose.mutate(nextPage); }
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (preview && confirmed && reason.trim().length >= 10) record.mutate(preview); }
  return <section className="card" data-testid="financial-binding-revisions">
    <h2>SKU link revisions</h2>
    <p className="muted">Revisions preserve the original report and financial amounts. They apply mappings that have already been approved; cost and batch warnings still require review.</p>
    <p role="status">{selection && selectedId ? "Showing revision " + selection.revision_number : "Showing original import links"}{history.data?.total ? " · Latest revision: " + history.data.total : ""}</p>
    <button className="button" type="button" onClick={() => choose(null)} disabled={!selectedId}>Show original links</button>
    <ErrorNote error={history.error} />
    {history.isPending ? <Loading what="Loading SKU link revisions" /> : null}
    {history.data?.total === 0 ? <EmptyState title="No link revisions recorded" /> : null}
    {history.data?.items.length ? <div className="table-wrap"><table className="table"><thead><tr><th>Revision</th><th>Recorded</th><th>Changed rows</th><th>Linked</th><th>Row reviews</th><th>Reason</th><th /></tr></thead><tbody>{history.data.items.map((revision) => <tr key={revision.id}>
      <td>{revision.revision_number}{revision.revision_number === history.data.total ? " (latest)" : ""}</td><td>{fmtDate(revision.created_at)}</td><td>{revision.changed_rows}</td><td>{revision.linked_rows}</td><td>{revision.review_rows}</td><td className="financial-revision-reason">{revision.reason}</td>
      <td><button type="button" className="button" disabled={selectedId === revision.id} onClick={() => choose(revision)}>Show revision {revision.revision_number}</button></td>
    </tr>)}</tbody></table></div> : null}
    {history.data && history.data.total > history.data.page_size ? <Pager page={history.data.page} pageSize={history.data.page_size} total={history.data.total} onPage={setPage} /> : null}
    {selection ? <div data-testid="financial-selected-revision"><h3>Selected revision {selection.revision_number}</h3><StatusBadge value={selection.status} />
      {selection.status === "REVIEW_REQUIRED" ? <Note tone="warn">This revision still requires review. Check source cost warnings, row issues and batch controls.</Note> : null}
      <KeyValues rows={[["Approved SKU links", selection.linked_rows.toLocaleString()], ["Rows needing review", selection.review_rows.toLocaleString()], ["Recorded by user", selection.recorded_by_user_id], ["Recorded", fmtDate(selection.created_at)], ["Reason", selection.reason]]} />
    </div> : null}
    {hasRole("DATA_OPERATOR") ? <div className="financial-binding-action">
      <h3>Review current approved links</h3>
      <p className="muted small">The preview compares current approvals with the latest recorded links, even if you are viewing an older revision. No changes are saved until you record the preview.</p>
      <button className="button" type="button" disabled={busy || history.isPending || history.isError} onClick={() => runPreview(1)}>{propose.isPending ? "Checking links…" : "Preview current SKU links"}</button>
      <ErrorNote error={propose.error ?? record.error} />
      {preview ? <div data-testid="financial-binding-preview">
        <h3>Proposed link changes</h3><StatusBadge value={preview.status} />
        <p>{preview.changed_rows.toLocaleString()} changed rows · {preview.linked_rows.toLocaleString()} linked · {preview.review_rows.toLocaleString()} rows needing review</p>
        <p className="muted small">Compared with {preview.expected_revision_id ? "the latest revision" : "the original import links"}. Original financial amounts and reporting details stay the same.</p>
        {preview.changed_rows === 0 ? <Note>No SKU links have changed. There is no new revision to record.</Note> : <>
          <div className="table-wrap"><table className="table"><thead><tr><th>Source row</th><th>Seller SKU</th><th>Previous links</th><th>Proposed links</th></tr></thead><tbody>{preview.changes.map((change) => <tr key={change.source_row_number}>
            <td>{change.source_row_number}</td><td className="mono">{change.seller_sku ?? "Unallocated"}</td><td><BindingEvidence value={change.before} /></td><td><BindingEvidence value={change.after} /></td>
          </tr>)}</tbody></table></div>
          {preview.changed_rows > preview.page_size ? <Pager page={preview.page} pageSize={preview.page_size} total={preview.changed_rows} onPage={runPreview} /> : null}
          <form onSubmit={submit}>
            <fieldset disabled={busy}>
              <label className="field"><span className="field__label">Reason for SKU link revision</span><textarea required minLength={10} maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} /><small>Explain why these approved links should replace the previous snapshot (10–1000 characters).</small></label>
              <label className="financial-check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I have reviewed the proposed SKU link changes</label>
              <button className="button button--primary" type="submit" disabled={!confirmed || reason.trim().length < 10}>{record.isPending ? "Recording…" : "Record SKU link revision"}</button>
            </fieldset>
          </form>
        </>}
      </div> : null}
    </div> : null}
  </section>;
}

function BindingEvidence({ value }: { value: FinancialBindingEvidence }) {
  return <div><StatusBadge value={value.mapping_status} /><details><summary>Mapping evidence</summary>
    <p className="financial-digest small">Product: {value.product_id ?? "Unresolved"}<br />Listing: {value.listing_id ?? "Unresolved"}<br />Approved: {fmtDate(value.mapping_snapshot.approved_at)}<br />Approved by: {value.mapping_snapshot.approved_by_user_id ?? "Unassigned"}</p>
    {value.review_reasons.length ? <ul>{value.review_reasons.map((reason) => <li key={reason}>{financialReason(reason)}</li>)}</ul> : <p>No row issues.</p>}
  </details></div>;
}
