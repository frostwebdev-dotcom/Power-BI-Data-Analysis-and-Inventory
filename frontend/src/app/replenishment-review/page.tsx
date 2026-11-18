"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ProductPicker } from "@/components/ProductPicker";
import { EmptyState, ErrorNote, Loading, Note, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { api } from "@/lib/api";
import type { Page } from "@/lib/api";
import { useVendor } from "@/lib/queries";
import type { Vendor } from "@/lib/types";
import { useReviewPage } from "@/lib/replenishment-review";
import { Reasons } from "../replenishment-recommendations/evidence";

export default function ReviewPage() { return <Suspense fallback={<Loading />}><Review /></Suspense>; }
function Review() {
  const params = useSearchParams();
  const [product, setProduct] = useState(params.get("product") ?? ""), [vendor, setVendor] = useState(params.get("vendor") ?? ""), [policy, setPolicy] = useState(params.get("policy") ?? "");
  const [page, setPage] = useState(1), [text, setText] = useState(""), [q, setQ] = useState("");
  const [status, setStatus] = useState(params.get("status") ?? ""), [approval, setApproval] = useState(params.get("approval") === "CHANGED" ? "CHANGED" : "CURRENT"), [shortfall, setShortfall] = useState(params.get("shortfall") === "1");
  const query = useReviewPage({ page, q, product_id: product, vendor_id: vendor, policy_id: policy, status, approval, shortfall_only: shortfall });
  return <div className="recommendations saved-review"><PageHeader title="Saved replenishment review" lede="Find the latest saved evaluation for each policy, inspect its historical evidence and start a fresh review." actions={<Link className="button" href="/vendor-comparisons">Compare vendors</Link>} />
    <Note>Every row is historical. Policy approval is checked against the latest decision; current mappings and source inputs are not rechecked here. Catalogue labels reflect current records. Quantities and reasons come from saved snapshots.</Note>
    <section className="card"><h2>Find saved evaluations</h2><form className="toolbar" onSubmit={e => { e.preventDefault(); setQ(text.trim()); setPage(1); }}><label className="field"><span className="field__label">Search product, catalogue, vendor or supplier SKU</span><input maxLength={200} value={text} onChange={e => setText(e.target.value)} /></label><button className="button" type="submit">Search saved evaluations</button></form>
      <div className="toolbar"><label className="field"><span className="field__label">Saved result</span><select value={status} onChange={e => { setStatus(e.target.value); setPage(1); }}><option value="">All results</option><option value="BLOCKED">Blocked</option><option value="REVIEW_REQUIRED">Review required</option><option value="NO_REORDER">No reorder</option></select></label>
        <label className="field"><span className="field__label">Approval relation</span><select value={approval} onChange={e => { setApproval(e.target.value); setPage(1); }}><option value="CURRENT">Same current approval</option><option value="CHANGED">Approval changed or revoked</option><option value="ALL">All saved approvals</option></select></label>
        <label className="checkbox-row"><input type="checkbox" checked={shortfall} onChange={e => { setShortfall(e.target.checked); setPage(1); }} />Known positive shortfalls only</label>
        <button className="button" onClick={() => void query.refetch()}>Reload saved records</button>
        {policy ? <button className="button" onClick={() => { setPolicy(""); setPage(1); }}>Clear policy filter</button> : null}
      </div>
      <details><summary>Filter by product or vendor</summary><ProductPicker value={product} onChange={id => { setProduct(id); setPage(1); }} /><VendorFilter value={vendor} onChange={id => { setVendor(id); setPage(1); }} /></details>
    </section>
    <section className="card"><h2>Latest saved record per policy</h2><ErrorNote error={query.error} />{query.isPending ? <Loading /> : null}
      {query.data?.total === 0 ? <EmptyState title="No matching saved evaluations" text="Change the filters or record a reviewed evaluation from an approved policy." /> : null}
      {query.data?.items.length ? <div className="table-wrap" role="region" aria-label="Latest saved replenishment evaluations" tabIndex={0}><table className="table"><thead><tr><th>Product / vendor</th><th>Saved result / approval</th><th>Historical quantity context</th><th>Sources / checks</th><th>Recorded / observed</th><th>Review</th></tr></thead><tbody>{query.data.items.map(item => <tr key={item.id}>
        <td><Link href={"/replenishment-recommendations?evaluation=" + encodeURIComponent(item.id)}>{item.identity.catalog_item_number} · {item.identity.product_name}</Link><div>{item.identity.vendor_name}</div><div className="mono small">{item.identity.vendor_sku}</div></td>
        <td><StatusBadge value={item.status} /><div>{item.approval_current ? "Same current approval" : "Approval changed or revoked"}</div></td>
        <td>{item.positive_shortfall === null ? "Shortfall unknown" : item.positive_shortfall ? "Known positive shortfall" : "No indicated shortfall"}<div>{item.stockout_risk_at_evaluation === null ? "Stockout risk unknown" : item.stockout_risk_at_evaluation ? "Stockout risk at evaluation" : "No indicated stockout risk at evaluation"}</div></td>
        <td>Supplier: {item.supplier_inputs.replaceAll("_", " ").toLowerCase()}<div>Commercial: {item.commercial_status?.replaceAll("_", " ").toLowerCase() ?? "not configured"}</div><div>{item.financial_import_id ? "Historical financial report included" : "No historical financial report"}</div><div className="small">Demand cutoff: {fmtDate(item.demand_window_end)}</div></td>
        <td>{fmtDate(item.recorded_at)}<div className="small">Observed {fmtDate(item.evaluated_at)}</div></td>
        <td><details><summary>Saved reasons ({item.reason_count})</summary><Reasons codes={item.reasons} />{item.reasons_truncated ? <p>More reasons are retained in the complete saved evaluation.</p> : null}<p>{item.reason}</p></details><Link href={"/replenishment-recommendations?policy=" + encodeURIComponent(item.policy_id)}>Start fresh evaluation</Link><div><Link href={"/vendor-comparisons?product=" + encodeURIComponent(item.product_id)}>Compare vendors</Link></div></td>
      </tr>)}</tbody></table></div> : null}
      {query.data ? <Pager page={page} pageSize={query.data.page_size} total={query.data.total} onPage={setPage} /> : null}
    </section>
  </div>;
}
function VendorFilter({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [text, setText] = useState(""), [search, setSearch] = useState<string | null>(null), [page, setPage] = useState(1);
  const selected = useVendor(value || null);
  const results = useQuery({ queryKey: ["review-vendor-search", search, page], enabled: search !== null, queryFn: () => api.get<Page<Vendor>>("/vendors", { q: search, page, page_size: 10 }) });
  return <div className="field"><label className="field"><span className="field__label">Find a vendor</span><input value={text} maxLength={200} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); setSearch(text.trim()); setPage(1); } }} /></label><button className="button" onClick={() => { setSearch(text.trim()); setPage(1); }}>Search vendors</button>
    <ErrorNote error={results.error ?? selected.error} />{selected.data ? <p>Selected: {selected.data.name} <button className="button" onClick={() => onChange("")}>Clear vendor filter</button></p> : null}
    {results.data?.items.map(vendor => <div className="candidate" key={vendor.id}><span>{vendor.name} · {vendor.code}</span><button className="button" onClick={() => onChange(vendor.id)}>Use vendor {vendor.name}</button></div>)}
    {results.data ? <Pager page={page} pageSize={results.data.page_size} total={results.data.total} onPage={setPage} /> : null}
  </div>;
}
