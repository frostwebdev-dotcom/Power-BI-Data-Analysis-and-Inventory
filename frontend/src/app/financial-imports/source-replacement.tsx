"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";

import { ErrorNote, Note, Pager, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { financialError, financialNumber, financialPeriod, financialReason, MAX_FINANCIAL_BYTES } from "@/lib/financial-imports";
import type { FinancialImport, FinancialReplacementPreview, FinancialUpload } from "@/lib/financial-imports";

export function FinancialReplacement({ batch }: { batch: FinancialImport }) {
  const { hasRole } = useAuth();
  const router = useRouter();
  const cache = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<FinancialReplacementPreview | null>(null);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const path = "/financial-imports/" + encodeURIComponent(batch.id);
  function formData() {
    if (!file || !file.size || file.size > MAX_FINANCIAL_BYTES || !file.name.toLowerCase().endsWith(".xlsx")) throw new Error("Choose a nonempty corrected Excel .xlsx file up to 8 MiB.");
    const data = new FormData(); data.append("file", file); return data;
  }
  const propose = useMutation({
    mutationFn: async (page: number) => { try { return await api.upload<FinancialReplacementPreview>(path + "/replacement-preview?page=" + page + "&page_size=25", formData()); } catch (error) { throw financialError(error); } },
    onMutate: () => { setPreview(null); setReason(""); setConfirmed(false); }, onSuccess: setPreview,
  });
  const record = useMutation({
    mutationFn: async (proposal: FinancialReplacementPreview) => { try { const data = formData(); data.append("preview_sha256", proposal.preview_sha256); data.append("reason", reason.trim()); return await api.upload<FinancialUpload>(path + "/replacements", data); } catch (error) { throw financialError(error); } },
    onSuccess: (outcome) => { void cache.invalidateQueries({ queryKey: ["financial-imports"] }); router.push("/financial-imports?import=" + encodeURIComponent(outcome.batch.id) + (outcome.duplicate ? "&duplicate=1" : "")); },
    onError: () => { setPreview(null); setConfirmed(false); void cache.invalidateQueries({ queryKey: ["financial-imports", batch.id] }); },
  });
  const busy = propose.isPending || record.isPending;
  function runPreview(page: number) { record.reset(); propose.mutate(page); }
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (preview && confirmed && reason.trim().length >= 10) record.mutate(preview); }
  if (!hasRole("DATA_OPERATOR") || batch.replaced_by_import_id) return null;
  return <section className="card financial-binding-action" data-testid="financial-source-replacement">
    <h2>Replace a corrected source report</h2>
    <p className="muted">Use a corrected export for the same reporting context. A replacement preserves this report and its original file, rows and audit history.</p>
    <p className="small">Uses existing context: {batch.store_name} · {financialPeriod(batch.profile_snapshot)} · {batch.profile_snapshot.reporting_timezone} · USD. Coverage assertions and worksheet stay the same.</p>
    <label className="field"><span className="field__label">Corrected source workbook</span><input type="file" accept=".xlsx" disabled={busy} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setPreview(null); setReason(""); setConfirmed(false); propose.reset(); record.reset(); }} /><small>Original Excel .xlsx export, up to 8 MiB.</small></label>
    <button type="button" className="button" disabled={busy || !file} onClick={() => runPreview(1)}>{propose.isPending ? "Checking corrected report…" : "Preview corrected report"}</button>
    <ErrorNote error={propose.error ?? record.error} />
    {preview ? <div data-testid="financial-replacement-preview">
      <h3>Replacement preview: {preview.original_filename}</h3><StatusBadge value={preview.status} />
      <Note tone="warn">This preview has not been recorded. Check changed totals, removed SKUs and all remaining warnings. Purchasing recommendations are not enabled.</Note>
      <p>{preview.previous_row_count.toLocaleString()} previous rows → {preview.row_count.toLocaleString()} replacement rows · {preview.linked_rows.toLocaleString()} approved links · {preview.review_rows.toLocaleString()} rows needing review</p>
      <p>{preview.added_skus.toLocaleString()} added seller SKUs · {preview.removed_skus.toLocaleString()} removed seller SKUs</p>
      <h3>Batch warnings in the corrected report</h3>
      {preview.issues.length ? <ul>{preview.issues.map((issue, index) => <li key={index}>{financialReason(issue.code)}</li>)}</ul> : <p>No batch-level warnings. Check row issues and controls below.</p>}
      <h3>Financial total changes</h3><p className="muted small">Difference is replacement minus previous reported total. The replacement check compares its own detail against its report total. Unknown amounts remain unknown.</p>
      <div className="table-wrap"><table className="table" data-testid="financial-replacement-controls"><thead><tr><th>Measure</th><th>Previous total</th><th>Replacement total</th><th>Difference</th><th>Replacement detail</th><th>Replacement check</th></tr></thead><tbody>{preview.controls.map((control) => { const money = !["sales_qty", "refund_qty", "total_units"].includes(control.field); return <tr key={control.field}><th scope="row">{control.field.replaceAll("_", " ")}</th>{[control.previous_total, control.replacement_total, control.difference, control.replacement_detail_total].map((value, index) => <td className="financial-number" key={index}>{financialNumber(value, money)}</td>)}<td><StatusBadge value={control.replacement_check} /></td></tr>; })}</tbody></table></div>
      <h3>Corrected source rows</h3>
      <div className="table-wrap"><table className="table"><thead><tr><th>Source row</th><th>Seller SKU</th><th>SKU link</th><th>Net sales</th><th>Cost</th><th>Reported profit</th><th>Calculated profit</th><th>Review reasons</th></tr></thead><tbody>{preview.rows.map((row) => <tr key={row.source_row_number}><td>{row.source_row_number}</td><td>{row.row_kind === "UNALLOCATED" ? "Unitemized (unallocated)" : row.seller_sku ?? "Missing SKU"}</td><td><StatusBadge value={row.mapping_status} /></td>{[row.net_sales, row.cost, row.reported_profit, row.computed_profit].map((value, index) => <td className="financial-number" key={index}>{financialNumber(value)}</td>)}<td className="financial-revision-reason">{row.review_reasons.length ? <ul>{row.review_reasons.map((value) => <li key={value}>{financialReason(value)}</li>)}</ul> : "No row issues"}</td></tr>)}</tbody></table></div>
      <Pager page={preview.page} pageSize={preview.page_size} total={preview.row_count} onPage={runPreview} />
      <form onSubmit={submit}><fieldset disabled={busy}>
        <label className="field"><span className="field__label">Reason for source report replacement</span><textarea required minLength={10} maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} /><small>Explain the correction (10–1000 characters).</small></label>
        <label className="financial-check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I reviewed the changed totals, removed SKUs and remaining warnings</label>
        <button type="submit" className="button button--primary" disabled={!confirmed || reason.trim().length < 10}>{record.isPending ? "Recording replacement…" : "Record corrected report"}</button>
      </fieldset></form>
    </div> : null}
  </section>;
}
