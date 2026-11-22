"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import type { FormEvent } from "react";

import { Drawer, EmptyState, ErrorNote, KeyValues, Loading, Note, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { financialError, financialNumber, financialPeriod, financialReason, MAX_FINANCIAL_BYTES, MONTHS, useFinancialImport, useFinancialImports, useFinancialRows } from "@/lib/financial-imports";
import type { FinancialControl, FinancialProfile, FinancialRow, FinancialUpload } from "@/lib/financial-imports";
import { FinancialBindings } from "./sku-link-revisions";
import { FinancialReplacement } from "./source-replacement";

const MAPPINGS = ["LINKED", "UNRESOLVED", "UNALLOCATED", "INVALID_SOURCE", "ASIN_CONFLICT"];
const LABELS: Record<string, string> = {
  sales_qty: "Sales quantity", refund_qty: "Refund quantity", total_units: "Net units",
  sales: "Sales", refunds: "Refunds", adjustments: "Adjustments", net_sales: "Net sales",
  fees: "Fees", ad_spend: "Advertising", cost: "Product cost", profit: "Reported profit",
};
function label(field: string) { return LABELS[field] ?? field.replaceAll("_", " "); }

export default function FinancialImportsPage() {
  return <Suspense fallback={<Loading />}><FinancialImports /></Suspense>;
}
function FinancialImports() {
  const params = useSearchParams();
  const identifier = params.get("import");
  return <div className="financial-imports">{identifier
    ? <ImportDetail key={identifier} identifier={identifier} duplicate={params.get("duplicate") === "1"} />
    : <ImportHistory />}</div>;
}

function ImportHistory() {
  const { hasRole } = useAuth();
  const [page, setPage] = useState(1);
  const [currentOnly, setCurrentOnly] = useState(false);
  const history = useFinancialImports(page, currentOnly);
  return <>
    <PageHeader title="Financial imports" lede="Import ConnectBooks profit-and-loss reports and review source totals, approved SKU links and cost warnings." />
    {hasRole("DATA_OPERATOR") ? <UploadFinancial /> : null}
    <section className="card">
      <h2>Import history</h2>
      <label className="financial-check"><input type="checkbox" checked={currentOnly} onChange={(event) => { setCurrentOnly(event.target.checked); setPage(1); }} />Only current source reports</label>
      <p className="muted small">History includes earlier source versions. Use current reports when comparing or aggregating financial results.</p>
      <ErrorNote error={history.error} />
      {history.isPending ? <Loading what="Loading financial imports" /> : null}
      {history.data?.items.length === 0 ? <EmptyState title="No financial imports yet" /> : null}
      {history.data && history.data.items.length > 0 ? <div className="table-wrap">
        <table className="table" data-testid="financial-history"><thead><tr>
          <th>Report</th><th>Store</th><th>Period</th><th>Original status</th><th>Rows</th><th>Original links</th><th>Original row reviews</th><th>Received</th>
        </tr></thead><tbody>{history.data.items.map((batch) => <tr key={batch.id}>
          <td><Link href={"/financial-imports?import=" + encodeURIComponent(batch.id)}>{batch.original_filename}</Link><div className="muted small">{batch.replaced_by_import_id ? <Link href={"/financial-imports?import=" + batch.replaced_by_import_id}>Superseded · Open replacement</Link> : "Current source report"}</div></td>
          <td>{batch.store_name}</td><td>{financialPeriod(batch.profile_snapshot)}<div className="muted small">{batch.profile_snapshot.reporting_timezone}</div></td>
          <td><StatusBadge value={batch.status} /></td><td>{batch.row_count.toLocaleString()}</td><td>{batch.linked_rows.toLocaleString()}</td>
          <td>{batch.review_rows.toLocaleString()}</td><td>{fmtDate(batch.received_at)}</td>
        </tr>)}</tbody></table>
        <Pager page={history.data.page} pageSize={history.data.page_size} total={history.data.total} onPage={setPage} />
      </div> : null}
    </section>
  </>;
}

function UploadFinancial() {
  const router = useRouter();
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState({ store: "", sourceStore: "", sheet: "Main sheet", marketplace: "", month: "", year: "", zone: "", ads: false });
  const [validationError, setValidationError] = useState<unknown>(null);
  const upload = useMutation({
    mutationFn: ({ file, profile }: { file: File; profile: FinancialProfile }) => {
      const body = new FormData();
      body.append("file", file);
      body.append("profile_json", JSON.stringify(profile));
      return api.upload<FinancialUpload>("/financial-imports", body);
    },
    onSuccess: async (result) => {
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      await client.invalidateQueries({ queryKey: ["financial-imports"] });
      router.push("/financial-imports?import=" + encodeURIComponent(result.batch.id) + (result.duplicate ? "&duplicate=1" : ""));
    },
  });
  function field(key: "store" | "sourceStore" | "sheet" | "marketplace" | "month" | "year" | "zone", value: string) {
    setForm((old) => ({ ...old, [key]: value }));
    setValidationError(null);
    if (upload.isError) upload.reset();
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    setValidationError(null);
    try {
      if (!file || !file.name.toLowerCase().endsWith(".xlsx") || file.size === 0 || file.size > MAX_FINANCIAL_BYTES) throw new Error("Choose a nonempty Excel .xlsx file up to 8 MiB.");
      if (!/^[0-9]{4}$/.test(form.year) || Number(form.year) < 1900 || Number(form.year) > 9998) throw new Error("Enter the report's four-digit year.");
      try { new Intl.DateTimeFormat("en", { timeZone: form.zone.trim() }).format(); }
      catch { throw new Error("Enter the reporting time zone used in ConnectBooks, such as America/New_York."); }
      const profile: FinancialProfile = {
        layout_version: "connectbooks-signed-pnl-xlsx-v1", sheet_name: form.sheet.trim(),
        store_name: form.store.trim(), source_store_name: form.sourceStore.trim(), marketplace_id: form.marketplace,
        currency: "USD", reporting_month: Number(form.month), reporting_year: Number(form.year),
        reporting_timezone: form.zone.trim(), advertising_coverage_confirmed: form.ads,
      };
      upload.mutate({ file, profile });
    } catch (error) { setValidationError(error); }
  }
  return <section className="card">
    <h2>Import a ConnectBooks report</h2>
    <p className="muted">Use the reporting period and time zone selected in ConnectBooks. The original file is retained unchanged. Supported currency: USD.</p>
    <form className="form" onSubmit={submit} data-testid="financial-upload">
      <ErrorNote error={validationError ?? financialError(upload.error)} />
      <fieldset className="financial-fields" disabled={upload.isPending}>
        <div className="grid grid--2">
          <label className="field"><span className="field__label">Store name in PRMS</span><input required maxLength={200} value={form.store} onChange={(e) => field("store", e.target.value)} /></label>
          <label className="field"><span className="field__label">Store value in report</span><input required maxLength={200} value={form.sourceStore} onChange={(e) => field("sourceStore", e.target.value)} /><small>Exact value in the report&apos;s Store column.</small></label>
          <label className="field"><span className="field__label">Worksheet</span><input required maxLength={100} value={form.sheet} onChange={(e) => field("sheet", e.target.value)} /></label>
          <label className="field"><span className="field__label">Marketplace</span><select required value={form.marketplace} onChange={(e) => field("marketplace", e.target.value)}><option value="">Choose marketplace</option><option value="ATVPDKIKX0DER">Amazon US</option></select></label>
          <label className="field"><span className="field__label">Report month</span><select required value={form.month} onChange={(e) => field("month", e.target.value)}><option value="">Choose month</option>{MONTHS.map((month, index) => <option key={month} value={index + 1}>{month}</option>)}</select></label>
          <label className="field"><span className="field__label">Report year</span><input required inputMode="numeric" pattern="[0-9]{4}" minLength={4} maxLength={4} value={form.year} onChange={(e) => field("year", e.target.value)} /></label>
          <label className="field"><span className="field__label">Report time zone</span><input required list="financial-time-zones" maxLength={100} placeholder="America/New_York" value={form.zone} onChange={(e) => field("zone", e.target.value)} /><small>Use the report&apos;s time zone, not your laptop&apos;s time zone.</small></label>
          <label className="field"><span className="field__label">Original Excel workbook</span><input ref={fileInput} type="file" required accept=".xlsx" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setValidationError(null); if (upload.isError) upload.reset(); }} /><small>Excel .xlsx, up to 8 MiB.</small></label>
        </div>
        <datalist id="financial-time-zones">{["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "UTC"].map((zone) => <option key={zone} value={zone} />)}</datalist>
        <label className="financial-check"><input type="checkbox" checked={form.ads} onChange={(e) => setForm((old) => ({ ...old, ads: e.target.checked }))} />Advertising costs in this report are complete</label>
        <p className="muted small">Leave unchecked if advertising coverage is unknown. The import will retain a review warning.</p>
        <button className="button button--primary" type="submit" disabled={upload.isPending}>{upload.isPending ? "Importing…" : "Import financial report"}</button>
      </fieldset>
    </form>
  </section>;
}

function ImportDetail({ identifier, duplicate }: { identifier: string; duplicate: boolean }) {
  const query = useFinancialImport(identifier);
  const [downloadError, setDownloadError] = useState<unknown>(null);
  const [downloading, setDownloading] = useState(false);
  const [bindingRevisionId, setBindingRevisionId] = useState("");
  const batch = query.data;
  async function download() {
    setDownloadError(null);
    setDownloading(true);
    try {
      const { blob, filename } = await api.blob("/financial-imports/" + encodeURIComponent(identifier) + "/raw");
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename ?? batch?.original_filename ?? "financial-report.xlsx";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { setDownloadError(financialError(error)); }
    finally { setDownloading(false); }
  }
  return <>
    <p><Link href="/financial-imports">All financial imports</Link></p>
    <ErrorNote error={query.error} />
    {query.isPending ? <Loading what="Loading financial report" /> : null}
    {batch ? <>
      <PageHeader title={batch.original_filename} lede={batch.store_name + " · " + financialPeriod(batch.profile_snapshot) + " · USD"} actions={<button type="button" className="button" disabled={downloading} onClick={download}>{downloading ? "Downloading…" : "Download original workbook"}</button>} />
      {batch.replaced_by_import_id ? <Note tone="warn">Historical report: this source was replaced. <Link href={"/financial-imports?import=" + batch.replaced_by_import_id}>Open its replacement</Link>. Original amounts and evidence remain available below.</Note> : null}
      {batch.supersedes_import_id ? <Note>This report replaces an earlier source. <Link href={"/financial-imports?import=" + batch.supersedes_import_id}>View the previous report</Link>. Reason: {batch.replacement_reason}</Note> : null}
      {duplicate ? <p className="note note--info" role="status">This file was already imported. You are viewing the existing record.</p> : null}
      <ErrorNote error={downloadError} />
      <section className="card" data-testid="financial-summary">
        <h2>Original import summary</h2>
        <StatusBadge value={batch.status} />
        {batch.status === "REVIEW_REQUIRED" ? <Note tone="warn">Review required. Check batch warnings and financial totals below, even when the row review count is zero.</Note> : null}
        <KeyValues rows={[
          ["Report period", financialPeriod(batch.profile_snapshot)],
          ["Report time zone", batch.profile_snapshot.reporting_timezone ?? "Unconfirmed"],
          ["Rows recorded", batch.row_count.toLocaleString()],
          ["Approved SKU links", batch.linked_rows.toLocaleString()],
          ["Rows needing review", batch.review_rows.toLocaleString()],
          ["Unallocated rows", batch.report_snapshot.unallocated_rows?.toLocaleString() ?? "Unavailable"],
          ["Received", fmtDate(batch.received_at)],
        ]} />
        <p className="muted">These financial records are available for review. Purchasing recommendations are not enabled.</p>
        <details><summary>Import reference and original-file verification</summary><KeyValues rows={[["Import ID", identifier], ["File SHA-256", <span className="financial-digest" key="hash">{batch.raw_sha256}</span>]]} /></details>
      </section>
      <section className="card" data-testid="financial-batch-checks">
        <h2>Batch warnings</h2>
        {Array.isArray(batch.report_snapshot.issues) ? batch.report_snapshot.issues.length
          ? <ul>{batch.report_snapshot.issues.map((issue, i) => <li key={i}>{financialReason(issue.code)} <span className="muted">({label(issue.field)})</span></li>)}</ul>
          : <p className="muted">No batch-level warnings were recorded. Review the row issues separately.</p>
          : <Note tone="warn">Batch checks are unavailable. Review is required.</Note>}
      </section>
      <section className="card">
        <h2>Financial totals</h2>
        <p className="muted small">Detail includes unallocated amounts. Refund and fee credits retain their signs. Unknown amounts remain unknown.</p>
        {Array.isArray(batch.report_snapshot.controls) && batch.report_snapshot.controls.length
          ? <ControlsTable controls={batch.report_snapshot.controls} />
          : <EmptyState title="Financial controls unavailable" text="This report needs review before its totals can be verified." />}
      </section>
      <FinancialReplacement batch={batch} />
      <FinancialBindings identifier={identifier} selectedId={bindingRevisionId} onSelect={setBindingRevisionId} current={!batch.replaced_by_import_id} />
      <FinancialRows key={bindingRevisionId} identifier={identifier} revisionId={bindingRevisionId} />
    </> : null}
  </>;
}

function ControlsTable({ controls }: { controls: FinancialControl[] }) {
  return <div className="table-wrap"><table className="table" data-testid="financial-controls"><thead><tr><th>Measure</th><th>Sum of detail</th><th>Report total</th><th>Difference</th><th>Result</th></tr></thead>
    <tbody>{controls.map((control) => { const money = !["sales_qty", "refund_qty", "total_units"].includes(control.field); return <tr key={control.field}>
      <th scope="row">{label(control.field)}</th><td className="financial-number">{financialNumber(control.detail_total, money)}</td><td className="financial-number">{financialNumber(control.reported_total, money)}</td><td className="financial-number">{financialNumber(control.difference, money)}</td><td><StatusBadge value={control.status} /></td>
    </tr>; })}</tbody></table></div>;
}

function FinancialRows({ identifier, revisionId }: { identifier: string; revisionId: string }) {
  const [page, setPage] = useState(1);
  const [reviewOnly, setReviewOnly] = useState(false);
  const [mapping, setMapping] = useState("");
  const [selected, setSelected] = useState<FinancialRow | null>(null);
  const rows = useFinancialRows(identifier, page, reviewOnly, mapping, revisionId);
  return <section className="card">
    <h2>Rows and review issues</h2>
    <p className="muted small">{revisionId ? "Showing the selected SKU link revision with original financial amounts." : "Showing the original SKU links and financial amounts recorded at upload."}</p>
    <div className="toolbar">
      <label className="financial-check"><input type="checkbox" checked={reviewOnly} onChange={(e) => { setReviewOnly(e.target.checked); setPage(1); }} />Only rows needing review</label>
      <label className="field"><span className="field__label">SKU link status</span><select value={mapping} onChange={(e) => { setMapping(e.target.value); setPage(1); }}><option value="">All link statuses</option>{MAPPINGS.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ").toLowerCase()}</option>)}</select></label>
    </div>
    <ErrorNote error={rows.error} />
    {rows.isPending ? <Loading what="Loading financial rows" /> : null}
    {rows.data ? <>
      <p className="muted small" role="status">{rows.data.total.toLocaleString()} rows{reviewOnly ? " needing review" : ""}{mapping ? " · " + mapping.replaceAll("_", " ").toLowerCase() : ""}</p>
      {rows.data.items.length ? <div className="table-wrap"><table className="table" data-testid="financial-rows"><thead><tr><th>Source row</th><th>Seller SKU</th><th>SKU link</th><th>Net sales</th><th>Cost</th><th>Fees</th><th>Advertising</th><th>Reported profit</th><th>Calculated profit</th><th>Review</th><th /></tr></thead>
        <tbody>{rows.data.items.map((row) => <tr key={row.id}>
          <td>{row.source_row_number}</td><td className="mono">{row.row_kind === "UNALLOCATED" ? "Unitemized (unallocated)" : row.seller_sku ?? "Missing SKU"}</td><td><StatusBadge value={row.mapping_status} /></td>
          {[row.net_sales, row.cost, row.fees, row.ad_spend, row.reported_profit, row.computed_profit].map((amount, i) => <td className="financial-number" key={i}>{financialNumber(amount)}</td>)}
          <td>{row.review_required ? <span className="badge badge--warn">Review required</span> : "No row issues"}</td>
          <td><button type="button" className="button button--ghost" aria-label={"Review source row " + row.source_row_number} onClick={() => setSelected(row)}>Details</button></td>
        </tr>)}</tbody></table></div> : <EmptyState title="No rows match these filters" />}
      <Pager page={rows.data.page} pageSize={rows.data.page_size} total={rows.data.total} onPage={setPage} />
    </> : null}
    {selected ? <RowDetail row={selected} onClose={() => setSelected(null)} /> : null}
  </section>;
}

function RowDetail({ row, onClose }: { row: FinancialRow; onClose: () => void }) {
  const source = row.source_snapshot;
  return <Drawer title={"Source row " + row.source_row_number} onClose={onClose} wide>
    <KeyValues rows={[
      ["Source item", source.source_item ?? "Missing"], ["Seller SKU", row.seller_sku ?? "Unallocated"],
      ["Reported ASIN", source.asin ?? "Unknown"], ["SKU link", <StatusBadge value={row.mapping_status} key="mapping" />],
      ["Product ID", row.product_id ?? "Unresolved"], ["Mapping priority", row.match_priority ?? "Unassigned"],
      ["Approval recorded", fmtDate(row.mapping_snapshot.approved_at)],
      ["Approved by user", row.mapping_snapshot.approved_by_user_id ?? "Unassigned"],
      ["Mapping method", row.mapping_snapshot.mapping_method?.replaceAll("_", " ").toLowerCase() ?? "Unassigned"],
    ]} />
    <h3>Review reasons</h3>
    {row.review_reasons.length ? <ul>{row.review_reasons.map((reason) => <li key={reason}>{financialReason(reason)}</li>)}</ul> : <p>No row-level issues were recorded.</p>}
    {source.issues.length ? <ul>{source.issues.map((issue, i) => <li key={i}>{label(issue.field)}: {financialReason(issue.code)}</li>)}</ul> : null}
    <h3>Original source amounts</h3>
    <KeyValues rows={Object.entries(source.amounts).map(([field, value]) => [label(field), <span className="financial-number" key={field}>{financialNumber(value, !["sales_qty", "refund_qty", "total_units"].includes(field))}</span>])} />
    <h3>Profitability ratios</h3>
    <KeyValues rows={[
      ["Reported margin (%)", financialNumber(source.reported_margin_percent, false)],
      ["Calculated margin (%)", financialNumber(source.computed_margin_percent, false)],
      ["Reported ROI (%)", financialNumber(source.reported_roi_percent, false)],
      ["Calculated ROI (%)", financialNumber(source.computed_roi_percent, false)],
    ]} />
    <p className="muted small">Calculated margin needs positive net sales. Calculated ROI needs positive cost. A source-reported zero does not supply a missing denominator.</p>
  </Drawer>;
}
