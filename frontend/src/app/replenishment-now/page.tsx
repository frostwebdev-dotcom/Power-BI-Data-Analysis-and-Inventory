"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { ProductPicker } from "@/components/ProductPicker";
import { EmptyState, ErrorNote, KeyValues, Loading, Note, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { quantity } from "@/lib/recommendations";
import { opportunityLabel, urgencyLabel, useCurrentChoices } from "@/lib/replenishment-now";
import type { CurrentCheck, CurrentPolicyChoice, CurrentRequest } from "@/lib/replenishment-now";
import { Evidence, Reasons } from "../replenishment-recommendations/evidence";

export default function CurrentPage() { return <Suspense fallback={<Loading />}><Current /></Suspense>; }
function Current() {
  const params = useSearchParams();
  const [product, setProduct] = useState(params.get("product") ?? ""), [page, setPage] = useState(1), [text, setText] = useState(""), [q, setQ] = useState("");
  const [selected, setSelected] = useState<CurrentPolicyChoice[]>([]), [message, setMessage] = useState("");
  const choices = useCurrentChoices(page, q, product);
  function toggle(choice: CurrentPolicyChoice) {
    setMessage("");
    setSelected(old => old.some(c => c.policy_id === choice.policy_id) ? old.filter(c => c.policy_id !== choice.policy_id) : old.length < 10 ? [...old, choice] : old);
  }
  const request: CurrentRequest = { choices: selected.map(c => ({ policy_id: c.policy_id, expected_approved_revision_id: c.approved_revision_id })) };
  return <div className="recommendations current-replenishment"><PageHeader title="Current replenishment check" lede="Recheck selected approved policies for stockout urgency and supplier review opportunities." actions={<Link className="button" href="/replenishment-review">Saved evaluations</Link>} />
    <Note>Checks use ingested source data at the displayed time. They do not refresh Amazon or supplier feeds. Unknown inputs stay unknown; stock is not reserved and purchasing eligibility remains disabled.</Note>
    <section className="card"><h2>Choose approved policies · {selected.length} / 10</h2><p className="muted">Choose up to ten policies explicitly. Selections are retained across search pages and filters; results cover only that selected set.</p>
      <form className="toolbar" onSubmit={e => { e.preventDefault(); setQ(text.trim()); setPage(1); }}><label className="field"><span className="field__label">Find product, catalogue, vendor or supplier SKU</span><input aria-label="Find product, catalogue, vendor or supplier SKU" maxLength={200} value={text} onChange={e => setText(e.target.value)} /></label><button className="button" type="submit">Search approved policies</button></form>
      <details><summary>Filter policy choices by product</summary><ProductPicker value={product} onChange={id => { setProduct(id); setPage(1); }} /></details>
      <button className="button" onClick={() => { setSelected([]); setMessage(""); void choices.refetch(); }}>Reload choices and clear selection</button>
      <ErrorNote error={choices.error} />{choices.isPending ? <Loading what="Loading approved choices" /> : null}
      {choices.data?.items.map(choice => <div className="candidate" key={choice.policy_id}><label className="checkbox-row"><input type="checkbox" checked={selected.some(c => c.policy_id === choice.policy_id)} disabled={selected.length >= 10 && !selected.some(c => c.policy_id === choice.policy_id)} onChange={() => toggle(choice)} />{choice.identity.catalog_item_number} · {choice.identity.vendor_name} · {choice.identity.vendor_sku} · approved version {choice.approved_revision_number}</label><Link href={"/purchasing-policies?policy=" + encodeURIComponent(choice.policy_id)}>Policy</Link></div>)}
      {choices.data?.total === 0 ? <EmptyState title="No current approved policies found" text="Change the filters or create and review the required policy. No policy is chosen automatically." /> : null}
      {choices.data ? <Pager page={page} pageSize={choices.data.page_size} total={choices.data.total} onPage={setPage} /> : null}
      {selected.length ? <ul aria-label="Policies selected for current check">{selected.map(c => <li key={c.policy_id}>{c.identity.catalog_item_number} · {c.identity.vendor_name} · {c.identity.vendor_sku} · version {c.approved_revision_number}{" "}<button className="button button--ghost" aria-label={"Remove " + c.identity.catalog_item_number + " " + c.identity.vendor_name} onClick={() => setSelected(old => old.filter(item => item.policy_id !== c.policy_id))}>Remove</button></li>)}</ul> : <p>No policies selected.</p>}
    </section>
    {message ? <Note tone="warn">{message}</Note> : null}
    {selected.length ? <CheckRunner key={JSON.stringify(request)} request={request} onStale={() => { setSelected([]); setMessage("A selected approval changed. Reload choices and select the policies again."); void choices.refetch(); }} /> : null}
  </div>;
}
function CheckRunner({ request, onStale }: { request: CurrentRequest; onStale: () => void }) {
  const [result, setResult] = useState<CurrentCheck | null>(null), [pending, setPending] = useState(false), [error, setError] = useState<unknown>(null), [inspect, setInspect] = useState("");
  const [filter, setFilter] = useState("ALL"), [opportunities, setOpportunities] = useState(false);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  async function run() {
    setPending(true); setResult(null); setInspect(""); setError(null);
    try { const value = await api.post<CurrentCheck>("/replenishment-now/preview", request); if (active.current) setResult(value); }
    catch (error) { if (active.current) { if (error instanceof ApiError && error.statusCode === 409) onStale(); else setError(error); } }
    finally { if (active.current) setPending(false); }
  }
  const shown = result?.items.filter(item => (filter === "ALL" || item.urgency === filter) && (!opportunities || item.opportunity === "REVIEW_OPPORTUNITY")) ?? [];
  const detail = result?.items.find(item => item.policy_id === inspect);
  return <section><button className="button button--primary" disabled={pending} onClick={() => void run()}>{pending ? "Checking current inputs…" : "Check selected policies now"}</button><ErrorNote error={error} />
    {result ? <section className="card" data-testid="current-replenishment-result"><h2>Selected policies · Checked results</h2>
      <Note>Urgency describes verified Amazon stock and demand at this check. Supplier review is separate: an urgent item may have no usable supplier offer. These counts are for the selected policies and can include multiple offers for one product. They are not catalogue-wide totals.</Note>
      <KeyValues rows={[["Checked at", fmtDate(result.evaluated_at)], ["Policies checked", quantity(result.summary.checked_policies)], ["Urgent at check", quantity(result.summary.urgent)], ["Other replenishment shortfalls", quantity(result.summary.replenish)], ["Unknown current inputs", quantity(result.summary.unknown)], ["No reorder indicated", quantity(result.summary.no_reorder)], ["Supplier review opportunities", quantity(result.summary.review_opportunities)]]} />
      <div className="toolbar"><label className="field"><span className="field__label">Show captured urgency</span><select aria-label="Show captured urgency" value={filter} onChange={e => setFilter(e.target.value)}><option value="ALL">All checked policies</option>{Object.entries(urgencyLabel).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></label><label className="checkbox-row"><input type="checkbox" checked={opportunities} onChange={e => setOpportunities(e.target.checked)} />Supplier review opportunities only</label></div>
      <p className="small muted">Display filters use this captured result. Press “Check selected policies now” again to recheck sources and freshness.</p>
      {shown.length === 0 ? <EmptyState title="No checked results match these filters" text="Change the display filters. No additional policies are evaluated automatically." /> : <div className="table-wrap" role="region" aria-label="Current selected-policy replenishment results" tabIndex={0}><table className="table"><thead><tr><th>Product / supplier</th><th>Urgency / supplier review</th><th>Source cutoff / checks</th><th>Per-pool Amazon quantity evidence</th><th>Inspect</th></tr></thead><tbody>{shown.map(item => <tr key={item.policy_id}>
        <td>{item.identity.catalog_item_number}<div>{item.identity.product_name}</div><div>{item.identity.vendor_name}</div><div className="mono small">{item.identity.vendor_sku}</div></td>
        <td><strong>{urgencyLabel[item.urgency]}</strong><div>{opportunityLabel[item.opportunity]}</div><Reasons codes={item.reasons} /></td>
        <td>Demand cutoff: {fmtDate(item.snapshot.readiness.demand_window_end)}<div>Amazon: <StatusBadge value={item.snapshot.readiness.status} /></div><div>Evaluation: <StatusBadge value={item.snapshot.status} /></div><div>Supplier: {item.snapshot.supplier_inputs.replaceAll("_", " ").toLowerCase()}</div><div>Commercial: {item.snapshot.commercial?.status.replaceAll("_", " ").toLowerCase() ?? "not configured"}</div></td>
        <td>{item.pools.map(pool => <details key={pool.pool_id}><summary>{pool.pool_id} · {pool.channel} · {urgencyLabel[pool.urgency]}</summary><KeyValues rows={[["Sold / window", quantity(pool.sales_base_units) + " base units / " + quantity(pool.window_days) + " days"], ["On hand", quantity(pool.on_hand_base_units)], ["Usable inbound", quantity(pool.usable_inbound_base_units)], ["Required", quantity(pool.required_units)], ["Net shortfall", quantity(pool.net_shortfall_units)], ["Exact supply days", pool.supply_days_numerator !== null && pool.supply_days_denominator !== null ? pool.supply_days_numerator + " / " + pool.supply_days_denominator : "Unknown"], ["Lead + safety days", quantity(pool.lead_plus_safety_days)]]} /><Reasons codes={pool.reasons} /></details>)}</td>
        <td><button className="button" onClick={() => setInspect(item.policy_id)}>Inspect {item.identity.catalog_item_number} {item.identity.vendor_name}</button><div><Link href={"/replenishment-recommendations?policy=" + encodeURIComponent(item.policy_id)}>Start fresh evaluation</Link></div><div><Link href={"/vendor-comparisons?product=" + encodeURIComponent(item.product_id)}>Compare vendors</Link></div></td>
      </tr>)}</tbody></table></div>}
    </section> : null}
    {detail ? <section data-testid="current-replenishment-detail"><h2>{detail.identity.catalog_item_number} · Checked evidence</h2><Evidence snapshot={detail.snapshot} /></section> : null}
  </section>;
}
