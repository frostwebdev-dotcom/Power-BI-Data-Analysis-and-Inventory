"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { InboundChoices } from "@/components/InboundChoices";
import { ProductPicker } from "@/components/ProductPicker";
import { EmptyState, ErrorNote, KeyValues, Loading, Note, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Page } from "@/lib/api";
import { financialNumber, useFinancialImports } from "@/lib/financial-imports";
import type { FinancialBindingRevision, FinancialImport } from "@/lib/financial-imports";
import { usePolicies } from "@/lib/purchasing-policies";
import type { PolicySummary } from "@/lib/purchasing-policies";
import { quantity } from "@/lib/recommendations";
import { sourceReceipt } from "@/lib/replenishment-review";
import type { ComparisonChoice, ComparisonRequest, VendorComparison } from "@/lib/replenishment-review";
import { Evidence, Reasons } from "../replenishment-recommendations/evidence";

export default function ComparisonPage() { return <Suspense fallback={<Loading />}><Comparison /></Suspense>; }
function Comparison() {
  const params = useSearchParams(), [product, setProduct] = useState(params.get("product") ?? "");
  return <div className="recommendations vendor-comparisons"><PageHeader title="Vendor comparison" lede="Compare explicitly selected approved policies, current supplier evidence and purchasing checks for one product." />
    <section className="card"><h2>Choose a product</h2><ProductPicker value={product} onChange={setProduct} /></section>
    {product ? <ProductComparison key={product} product={product} /> : <Note>Select a product to load its policies. No offer is selected automatically.</Note>}
  </div>;
}
function ProductComparison({ product }: { product: string }) {
  const [page, setPage] = useState(1), [reportPage, setReportPage] = useState(1), [report, setReport] = useState("");
  const [choices, setChoices] = useState<(ComparisonChoice & { label: string })[]>([]), [order, setOrder] = useState<ComparisonRequest["order_by"]>("VENDOR_NAME"), [message, setMessage] = useState("");
  const policies = usePolicies(page, product), reports = useFinancialImports(reportPage, true);
  const selected = useQuery({ queryKey: ["financial-imports", report], enabled: Boolean(report), queryFn: () => api.get<FinancialImport>("/financial-imports/" + encodeURIComponent(report)) });
  const bindings = useQuery({ queryKey: ["financial-imports", report, "bindings", 1], enabled: Boolean(report), queryFn: () => api.get<Page<FinancialBindingRevision>>("/financial-imports/" + encodeURIComponent(report) + "/binding-revisions", { page: 1, page_size: 25 }) });
  const financialReady = !report || Boolean(selected.data && !selected.data.replaced_by_import_id && bindings.data && !selected.isFetching && !bindings.isFetching && !selected.error && !bindings.error);
  const request: ComparisonRequest | null = choices.length && financialReady ? { product_id: product, choices: choices.map(({ policy_id, expected_approved_revision_id, inbound }) => ({ policy_id, expected_approved_revision_id, ...(inbound ? { inbound } : {}) })), order_by: order, ...(report ? { financial: { import_id: report, expected_binding_revision_id: bindings.data?.items[0]?.id ?? null } } : {}) } : null;
  function toggle(policy: PolicySummary) {
    setMessage("");
    if (choices.some(c => c.policy_id === policy.policy_id)) setChoices(old => old.filter(c => c.policy_id !== policy.policy_id));
    else if (policy.approved && choices.length < 10) setChoices(old => [...old, { policy_id: policy.policy_id, expected_approved_revision_id: policy.approved!.id, label: policy.identity.vendor_name + " · " + policy.identity.vendor_sku + " · version " + policy.approved!.revision_number }]);
  }
  return <><section className="card"><h2>Select approved offers · {choices.length} / 10</h2><p className="muted">Selections are retained across pages. Comparison covers only the policies you select.</p>
    <button className="button" onClick={() => { setChoices([]); setMessage(""); void policies.refetch(); }}>Reload policy choices and clear selection</button>
    <ErrorNote error={policies.error} />{policies.isPending ? <Loading /> : null}
    {policies.data?.items.map(policy => <div className="candidate" key={policy.policy_id}><label className="checkbox-row"><input type="checkbox" checked={choices.some(c => c.policy_id === policy.policy_id)} disabled={!policy.approved || choices.length >= 10 && !choices.some(c => c.policy_id === policy.policy_id)} onChange={() => toggle(policy)} />{policy.identity.vendor_name} · {policy.identity.vendor_sku} · {policy.approved ? "approved version " + policy.approved.revision_number : "no current approval"}</label><Link href={"/purchasing-policies?policy=" + encodeURIComponent(policy.policy_id)}>Policy</Link></div>)}
    {policies.data?.total === 0 ? <EmptyState title="No policies found" text="Create and approve the product’s supplier policies before comparing them." /> : null}
    {policies.data ? <Pager page={page} pageSize={policies.data.page_size} total={policies.data.total} onPage={setPage} /> : null}
    {choices.length ? <ul aria-label="Selected offer approvals">{choices.map(c => <li key={c.policy_id}>{c.label} <button className="button button--ghost" onClick={() => setChoices(old => old.filter(choice => choice.policy_id !== c.policy_id))}>Remove {c.label}</button></li>)}</ul> : <p className="muted">No offers selected.</p>}
  </section>
  {choices.map(c => <div key={c.policy_id}><h3>{c.label}</h3><InboundChoices policy={c.policy_id} approval={c.expected_approved_revision_id} value={c.inbound?.revision_ids ?? []} onChange={ids => setChoices(old => old.map(item => item.policy_id === c.policy_id ? { ...item, inbound: ids.length ? { revision_ids: ids } : undefined } : item))} /></div>)}
  <details className="card"><summary>Optional historical financial report</summary><Note>Historical figures are shown separately from current supplier prices. No report is selected automatically.</Note>
    <button className="button" onClick={() => setReport("")}>Use no financial report</button><p data-testid="comparison-selected-report">{report ? selected.data?.original_filename ?? "Loading selected report…" : "No financial report selected"}</p>
    <ErrorNote error={reports.error ?? selected.error ?? bindings.error} />
    {selected.data?.replaced_by_import_id ? <Note tone="warn">This report was replaced. Choose a current report.</Note> : null}
    {reports.data?.items.map(item => <div className="candidate" key={item.id}><div>{item.original_filename} · {item.store_name} · USD<div className="small">{fmtDate(item.period_start)} – {fmtDate(item.period_end)}</div></div><button className="button" disabled={item.id === report} onClick={() => setReport(item.id)}>Select report</button></div>)}
    {reports.data ? <Pager page={reportPage} pageSize={reports.data.page_size} total={reports.data.total} onPage={setReportPage} /> : null}
  </details>
  <section className="card"><label className="field"><span className="field__label">Comparison order</span><select aria-label="Comparison order" value={order} onChange={e => setOrder(e.target.value as ComparisonRequest["order_by"])}><option value="VENDOR_NAME">Vendor name</option><option value="BASE_PRICE">Verified reported base price</option></select></label><Note>Price ordering requires comparable approved units and tax treatment. Availability, quantities, terms and landed costs still require review.</Note></section>
  {message ? <Note tone="warn">{message}</Note> : null}
  {request ? <ComparisonRunner key={JSON.stringify(request)} request={request} onStale={() => { setChoices([]); setMessage("The selected approval or source changed. Reload choices and select the offers again."); void policies.refetch(); if (report) { void selected.refetch(); void bindings.refetch(); } }} /> : <Note>Select at least one approved policy to preview a comparison.</Note>}
  </>;
}
function ComparisonRunner({ request, onStale }: { request: ComparisonRequest; onStale: () => void }) {
  const [result, setResult] = useState<VendorComparison | null>(null), [offerId, setOfferId] = useState(""), [pending, setPending] = useState(false), [error, setError] = useState<unknown>(null);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  async function compare() {
    setPending(true); setResult(null); setOfferId(""); setError(null);
    try { const value = await api.post<VendorComparison>("/vendor-comparisons/preview", request); if (active.current) setResult(value); }
    catch (error) { if (active.current) { if (error instanceof ApiError && error.statusCode === 409) onStale(); else setError(error); } }
    finally { if (active.current) setPending(false); }
  }
  const offer = result?.offers.find(item => item.policy_id === offerId);
  return <section data-testid="comparison-review"><button className="button button--primary" disabled={pending} onClick={() => void compare()}>{pending ? "Comparing…" : "Preview vendor comparison"}</button><ErrorNote error={error} />
    {result ? <><section className="card" data-testid="vendor-comparison-result"><h2>{result.catalog_item_number} · Selected offers</h2><Note>Comparison is an unsaved observation. Each offer has its own coverage policy and constraints. Stock is not reserved.</Note>
      <KeyValues rows={[["Observed", fmtDate(result.evaluated_at)], ["Selected offers", quantity(result.selected_count)], ["Comparable prices", quantity(result.comparable_price_count)], ["Price comparison", <StatusBadge key="prices" value={result.price_comparison_status} />], ["Applied order", result.applied_order === "BASE_PRICE" ? "Verified reported base price" : "Vendor name"]]} /><Reasons codes={result.issues} />
      <div className="table-wrap" role="region" aria-label="Selected vendor offer comparison" tabIndex={0}><table className="table"><thead><tr><th>Vendor / SKU</th><th>Source file received</th><th>Availability / stock</th><th>Reported price USD</th><th>Base units per price unit</th><th>Price per base unit USD</th><th>Tax basis</th><th>Evaluation / commercial checks</th><th>Evidence</th></tr></thead><tbody>{result.offers.map(item => <tr key={item.policy_id}>
        <td>{item.identity.vendor_name}<div className="mono small">{item.identity.vendor_sku}</div><Reasons codes={item.comparison_reasons} /></td><td>{fmtDate(sourceReceipt(item))}</td>
        <td>{typeof item.snapshot.supplier?.availability === "string" ? item.snapshot.supplier.availability : "Unknown"}<div>{quantity(typeof item.snapshot.supplier?.stock_base_units === "number" ? item.snapshot.supplier.stock_base_units : null)} base units</div></td>
        <td>{financialNumber(item.base_price?.reported_price)}</td><td>{quantity(item.base_price?.base_units_per_price_unit)}</td><td>{item.base_price ? <>{item.base_price.display_is_rounded ? "≈ " : ""}{financialNumber(item.base_price.display_per_base_unit, false)}<details><summary>Exact price ratio</summary>{item.base_price.exact_numerator} / {item.base_price.exact_denominator} USD</details>{!item.price_comparable ? <div className="small">Comparison unavailable</div> : null}</> : "Unknown"}</td>
        <td>{item.base_price ? item.base_price.includes_tax ? "Includes stated tax" : "Excludes stated tax" : "Unknown"}</td><td><StatusBadge value={item.snapshot.status} /><div>{item.snapshot.commercial?.status ?? "Commercial checks not configured"}</div></td>
        <td><button className="button" onClick={() => setOfferId(item.policy_id)}>Inspect {item.identity.vendor_name}</button><div><Link href={"/replenishment-recommendations?policy=" + encodeURIComponent(item.policy_id)}>Start fresh evaluation</Link></div></td>
      </tr>)}</tbody></table></div>
    </section>{offer ? <section data-testid="compared-offer-detail"><h2>{offer.identity.vendor_name} · Captured evidence</h2><Evidence snapshot={offer.snapshot} /></section> : null}</> : null}
  </section>;
}
