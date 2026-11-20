"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { InboundChoices } from "@/components/InboundChoices";
import { ProductPicker } from "@/components/ProductPicker";
import { EmptyState, ErrorNote, KeyValues, Loading, Note, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFinancialImports } from "@/lib/financial-imports";
import type { FinancialBindingRevision, FinancialImport } from "@/lib/financial-imports";
import { policyPath, usePolicies, usePolicy } from "@/lib/purchasing-policies";
import { hasUnsafeQuantity, useRecommendation, useRecommendations } from "@/lib/recommendations";
import type { EvaluationPreview, EvaluationRequest, RecordRequest, SavedEvaluation } from "@/lib/recommendations";
import { Evidence } from "./evidence";

export default function RecommendationsPage() { return <Suspense fallback={<Loading />}><Recommendations /></Suspense>; }
function Recommendations() {
  const params = useSearchParams(), id = params.get("evaluation"), policy = params.get("policy");
  return <div className="recommendations">{id ? <Detail key={id} id={id} /> : policy ? <NewEvaluation key={policy} id={policy} /> : <History />}</div>;
}
function productLabel(row: SavedEvaluation) {
  const product = row.snapshot.approved_policy.review_context?.product;
  if (product && typeof product === "object" && "catalog_item_number" in product && typeof product.catalog_item_number === "string") return product.catalog_item_number;
  return row.product_id;
}
function History() {
  const [page, setPage] = useState(1), [policyPage, setPolicyPage] = useState(1), [product, setProduct] = useState(""), [policy, setPolicy] = useState(""), [status, setStatus] = useState("");
  const query = useRecommendations(page, product, policy, status), policies = usePolicies(policyPage, product);
  return <><PageHeader title="Recommendations" lede="Review approved assumptions, observed stock and historical financial context before recording an evaluation." />
    <Note>Saved evaluations are historical review evidence. They do not authorize a purchase.</Note>
    <section className="card"><h2>Choose a policy to evaluate</h2><ProductPicker value={product} onChange={id => { setProduct(id); setPolicy(""); setPage(1); setPolicyPage(1); }} />
      <ErrorNote error={policies.error} />{policies.isPending ? <Loading /> : null}
      {policies.data?.items.map(item => <div className="candidate" key={item.policy_id}><div><strong>{item.identity.catalog_item_number} · {item.identity.product_name}</strong><div>{item.identity.vendor_name} · {item.identity.vendor_sku}</div><div className="small">{item.approved ? "Approved version " + item.approved.revision_number : "No current approval"}</div></div><div className="toolbar"><button className="button" type="button" onClick={() => { setPolicy(item.policy_id); setPage(1); }}>Filter history</button>{item.approved ? <Link className="button button--primary" href={"/replenishment-recommendations?policy=" + encodeURIComponent(item.policy_id)}>Evaluate policy</Link> : <Link href={"/purchasing-policies?policy=" + encodeURIComponent(item.policy_id)}>Review policy</Link>}</div></div>)}
      {policies.data?.total === 0 ? <EmptyState title="No policies found" text="Create and approve a purchasing policy to evaluate it." /> : null}
      {policies.data ? <Pager page={policyPage} pageSize={policies.data.page_size} total={policies.data.total} onPage={setPolicyPage} /> : null}
    </section>
    <section className="card"><h2>Saved evaluations</h2><div className="toolbar"><label>Status <select aria-label="Status" value={status} onChange={e => { setStatus(e.target.value); setPage(1); }}><option value="">All statuses</option>{["BLOCKED", "REVIEW_REQUIRED", "NO_REORDER"].map(s => <option key={s} value={s}>{s.replaceAll("_", " ")}</option>)}</select></label>{policy ? <button className="button" onClick={() => { setPolicy(""); setPage(1); }}>Clear policy filter</button> : null}</div>
      <ErrorNote error={query.error} />{query.isPending ? <Loading /> : null}
      {query.data?.total === 0 ? <EmptyState title="No saved evaluations" text="Choose an approved policy above to preview current source evidence." /> : null}
      {query.data?.items.length ? <div className="table-wrap" role="region" aria-label="Saved evaluations" tabIndex={0}><table className="table"><thead><tr><th>Product / evaluation</th><th>Status</th><th>Approved version</th><th>Recorded</th><th>Reason</th></tr></thead><tbody>{query.data.items.map(row => <tr key={row.id}><td><Link href={"/replenishment-recommendations?evaluation=" + encodeURIComponent(row.id)}>{productLabel(row)}</Link><div className="mono small">{row.id}</div></td><td><StatusBadge value={row.status} /></td><td>{row.snapshot.approved_policy.revision_number}</td><td>{fmtDate(row.recorded_at)}</td><td>{row.reason}</td></tr>)}</tbody></table></div> : null}
      {query.data ? <Pager page={page} pageSize={query.data.page_size} total={query.data.total} onPage={setPage} /> : null}
    </section>
  </>;
}
function Detail({ id }: { id: string }) {
  const query = useRecommendation(id), saved = query.data;
  return <><p><Link href="/replenishment-recommendations">All recommendations</Link></p><PageHeader title="Saved evaluation" lede="The inputs and result below preserve the evidence at recording time." /><ErrorNote error={query.error} />{query.isPending ? <Loading /> : null}
    {saved ? <><section className="card" data-testid="saved-evaluation"><h2>{productLabel(saved)}</h2><KeyValues rows={[["Recorded", fmtDate(saved.recorded_at)], ["Recorded by", saved.recorded_by_user_id], ["Reason", saved.reason], ["Snapshot digest", saved.snapshot_sha256], ["Evidence digest", saved.evidence_sha256]]} /><Link className="button" href={"/replenishment-recommendations?policy=" + encodeURIComponent(saved.policy_id)}>Start a new evaluation</Link></section><Evidence snapshot={saved.snapshot} /></> : null}
  </>;
}
function NewEvaluation({ id }: { id: string }) {
  const query = usePolicy(id), [report, setReport] = useState(""), [reportPage, setReportPage] = useState(1), [locked, setLocked] = useState(false), [frozen, setFrozen] = useState<EvaluationRequest | null>(null), [session, setSession] = useState(0), [message, setMessage] = useState("");
  const [inbound, setInbound] = useState<string[]>([]);
  const reports = useFinancialImports(reportPage, true);
  const selected = useQuery({ queryKey: ["financial-imports", report], enabled: Boolean(report), queryFn: () => api.get<FinancialImport>("/financial-imports/" + encodeURIComponent(report)) });
  const bindings = useQuery({ queryKey: ["financial-imports", report, "bindings", 1], enabled: Boolean(report), queryFn: () => api.get<Page<FinancialBindingRevision>>("/financial-imports/" + encodeURIComponent(report) + "/binding-revisions", { page: 1, page_size: 25 }) });
  const policy = query.data, approval = policy?.approved;
  const reportReady = !report || Boolean(selected.data && !selected.data.replaced_by_import_id && bindings.data && !selected.isFetching && !bindings.isFetching && !selected.error && !bindings.error);
  const request: EvaluationRequest | null = approval && !query.isFetching && !query.error && reportReady ? { expected_approved_revision_id: approval.id, ...(inbound.length ? { inbound: { revision_ids: inbound } } : {}), ...(report ? { financial: { import_id: report, expected_binding_revision_id: bindings.data?.items[0]?.id ?? null } } : {}) } : null;
  const effectiveRequest = locked ? frozen : request;
  const contextKey = JSON.stringify([effectiveRequest, session]);
  return <><p><Link href="/replenishment-recommendations">All recommendations</Link></p><PageHeader title="Review recommendation" lede={policy ? policy.identity.catalog_item_number + " · " + policy.identity.product_name + " · " + policy.identity.vendor_sku : undefined} actions={<button className="button" disabled={locked} onClick={() => { setSession(s => s + 1); void query.refetch(); if (report) { void selected.refetch(); void bindings.refetch(); } }}>Reload evidence choices</button>} />
    <ErrorNote error={query.error} />{query.isPending ? <Loading /> : null}
    {policy ? <section className="card"><h2>Approved policy</h2><p>{approval ? "Version " + approval.revision_number : "No current approval. Preview requires an approved policy."}</p><Link href={"/purchasing-policies?policy=" + encodeURIComponent(id)}>Open policy and assumptions</Link></section> : null}
    <section className="card"><h2>Optional historical financial report</h2><Note>Choose a report explicitly to include historical financial context. Leaving it unselected keeps profitability unknown.</Note>
      <button className="button" disabled={locked} onClick={() => setReport("")}>Use no financial report</button><p data-testid="selected-report">{report ? selected.data ? selected.data.original_filename + " · " + selected.data.store_name + " · USD · " + fmtDate(selected.data.period_start) + " – " + fmtDate(selected.data.period_end) : "Loading selected report…" : "No financial report selected"}</p>
      <ErrorNote error={reports.error ?? selected.error ?? bindings.error} />{report && selected.data?.replaced_by_import_id ? <Note tone="warn">This report was replaced. Choose a current report before previewing.</Note> : null}
      {reports.data?.items.map(item => <div className="candidate" key={item.id}><div><strong>{item.original_filename}</strong><div>{item.store_name} · USD · {fmtDate(item.period_start)} – {fmtDate(item.period_end)}</div><StatusBadge value={item.status} /></div><button className="button" disabled={locked || report === item.id} onClick={() => setReport(item.id)}>Select report</button></div>)}
      {reports.data?.total === 0 ? <p className="muted">No current financial reports are recorded.</p> : null}
      {reports.data ? <Pager page={reportPage} pageSize={reports.data.page_size} total={reports.data.total} onPage={page => { if (!locked) setReportPage(page); }} /> : null}
    </section>
    {approval ? <InboundChoices key={approval.id + session} policy={id} approval={approval.id} value={inbound} onChange={setInbound} disabled={locked} /> : null}
    {message ? <Note tone="warn">{message}</Note> : null}
    {policy && effectiveRequest ? <EvaluationPanel key={contextKey} policyId={id} request={effectiveRequest} onLock={value => { if (value) setFrozen(effectiveRequest); setLocked(value); }} onStale={() => { setInbound([]); setMessage("Evidence changed. Preview again and review the latest inputs before recording."); setSession(s => s + 1); void query.refetch(); if (report) { void selected.refetch(); void bindings.refetch(); } }} /> : null}
  </>;
}
function EvaluationPanel({ policyId, request, onLock, onStale }: { policyId: string; request: EvaluationRequest; onLock: (value: boolean) => void; onStale: () => void }) {
  const { hasRole } = useAuth(), router = useRouter(), cache = useQueryClient();
  const [preview, setPreview] = useState<EvaluationPreview | null>(null), [reason, setReason] = useState(""), [ack, setAck] = useState(false), [pending, setPending] = useState(false), [attempt, setAttempt] = useState<RecordRequest | null>(null), [error, setError] = useState<unknown>(null);
  async function loadPreview() {
    setPending(true); setError(null); setPreview(null); setAck(false); setReason("");
    try { setPreview(await api.post<EvaluationPreview>(policyPath(policyId) + "/recommendation-preview", request)); }
    catch (error) { setError(error); }
    finally { setPending(false); }
  }
  async function save() {
    if (!preview) return;
    const payload = attempt ?? { ...request, request_id: crypto.randomUUID(), expected_evidence_sha256: preview.evidence_sha256, reason: reason.trim(), acknowledge_review: true as const };
    setAttempt(payload); setPending(true); setError(null); onLock(true);
    try {
      const saved = await api.post<SavedEvaluation>(policyPath(policyId) + "/recommendations", payload);
      await Promise.all([cache.invalidateQueries({ queryKey: ["recommendations"] }), cache.invalidateQueries({ queryKey: ["replenishment-review"] })]);
      onLock(false); router.push("/replenishment-recommendations?evaluation=" + encodeURIComponent(saved.id));
    } catch (error) {
      if (error instanceof ApiError && error.statusCode >= 400 && error.statusCode < 500) {
        setAttempt(null); onLock(false);
        if (error.statusCode === 409) { setPreview(null); setAck(false); setReason(""); setError(new Error("Evidence changed. Reload the choices and preview again before recording.")); onStale(); }
        else setError(error);
      } else setError(new Error("The save result is unconfirmed. Retry the same save below to check it without creating a duplicate."));
    } finally { setPending(false); }
  }
  return <section data-testid="evaluation-review"><ErrorNote error={error} />
    <button className="button button--primary" disabled={pending || Boolean(attempt)} onClick={() => void loadPreview()}>{pending && !attempt ? "Loading preview…" : "Preview evaluation"}</button>
    {preview ? <><Evidence snapshot={preview.snapshot} />{hasRole("DATA_OPERATOR", "PURCHASING_MANAGER") ? <section className="card"><h2>Record historical evaluation</h2><Note>Recording preserves this reviewed snapshot. Purchasing eligibility remains false.</Note>
      <label className="field"><span className="field__label">Reason for recording</span><textarea value={reason} disabled={Boolean(attempt)} maxLength={1000} onChange={e => setReason(e.target.value)} /></label>
      <label className="checkbox-row"><input type="checkbox" checked={ack} disabled={Boolean(attempt)} onChange={e => setAck(e.target.checked)} />I reviewed the inputs, quantities, unknowns and further purchasing checks</label>
      {attempt ? <Note tone="warn">A save has been attempted. Keep the same request until its result is confirmed.</Note> : null}
      <button className="button button--primary" disabled={pending || hasUnsafeQuantity(preview.snapshot) || !ack || reason.trim().length < 10} onClick={() => void save()}>{pending && attempt ? "Saving…" : attempt ? "Retry same save" : "Record evaluation"}</button>
    </section> : <Note>You have read-only access. You can preview evidence and review saved evaluations.</Note>}</> : null}
  </section>;
}
