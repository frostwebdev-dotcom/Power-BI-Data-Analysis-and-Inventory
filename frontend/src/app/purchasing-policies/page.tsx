"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ProductPicker } from "@/components/ProductPicker";
import { EmptyState, ErrorNote, KeyValues, Loading, Note, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { usePolicies, usePolicy, usePolicyHistory, usePolicyListings } from "@/lib/purchasing-policies";
import type { PolicyRevision, VendorProductChoice } from "@/lib/purchasing-policies";
import { DefinitionView, MappingEvidence, reviewListings } from "./definition";
import { PolicyEditor, VendorChoice } from "./editor";
import { PolicyReviewPanel } from "./review";

export default function PurchasingPoliciesPage() { return <Suspense fallback={<Loading />}><Policies /></Suspense>; }
function Policies() {
  const params = useSearchParams();
  const id = params.get("policy");
  return <div className="purchasing-policies">{id ? <PolicyDetail key={id} id={id} /> : params.get("new") === "1" ? <NewPolicy /> : <PolicyHistory />}</div>;
}
function PolicyHistory() {
  const { hasRole } = useAuth();
  const [page, setPage] = useState(1), [product, setProduct] = useState("");
  const query = usePolicies(page, product);
  return <>
    <PageHeader title="Purchasing policies" lede="Keep replenishment assumptions, approval decisions and their history together." actions={hasRole("DATA_OPERATOR", "PURCHASING_MANAGER") ? <Link className="button button--primary" href="/purchasing-policies?new=1">New policy</Link> : null} />
    <Note>Approval records policy assumptions. Current stock, shipment arrivals and purchasing recommendations still need separate verification.</Note>
    <details className="card"><summary>Filter policies by product</summary><ProductPicker value={product} onChange={id => { setProduct(id); setPage(1); }} /></details>
    <section className="card"><h2>Policy history</h2><ErrorNote error={query.error} />{query.isPending ? <Loading what="Loading purchasing policies" /> : null}
      {query.data?.total === 0 ? <EmptyState title="No purchasing policies found" text="Create a draft once the product and supplier SKU are identified." /> : null}
      {query.data?.items.length ? <div className="table-wrap" role="region" aria-label="Purchasing policy history table" tabIndex={0}><table className="table" data-testid="policy-history"><thead><tr><th>Product</th><th>Supplier SKU</th><th>Editing head</th><th>Approved policy</th><th>Last recorded</th></tr></thead><tbody>{query.data.items.map(policy => <tr key={policy.policy_id}>
        <td><Link href={"/purchasing-policies?policy=" + encodeURIComponent(policy.policy_id)}>{policy.identity.catalog_item_number} · {policy.identity.product_name}</Link></td><td>{policy.identity.vendor_name}<div className="mono small">{policy.identity.vendor_sku}</div></td>
        <td>Version {policy.head.revision_number} · <StatusBadge value={policy.head.kind} /></td><td>{policy.approved ? "Version " + policy.approved.revision_number : "No current approval"}</td><td>{fmtDate(policy.head.recorded_at)}</td>
      </tr>)}</tbody></table></div> : null}
      {query.data ? <Pager page={query.data.page} pageSize={query.data.page_size} total={query.data.total} onPage={setPage} /> : null}
    </section>
  </>;
}
function NewPolicy() {
  const { hasRole } = useAuth();
  const [productId, setProductId] = useState("");
  return <><p><Link href="/purchasing-policies">All purchasing policies</Link></p><PageHeader title="New purchasing policy" lede="Choose an identified product and supplier SKU, then enter the agreed assumptions." />
    {hasRole("DATA_OPERATOR", "PURCHASING_MANAGER") ? <><section className="card"><h2>Choose the product</h2><ProductPicker value={productId} onChange={setProductId} /></section>{productId ? <NewPolicyProduct key={productId} productId={productId} /> : null}</> : <Note>You have read-only access to policies.</Note>}
  </>;
}
function NewPolicyProduct({ productId }: { productId: string }) {
  const router = useRouter();
  const [vendor, setVendor] = useState<VendorProductChoice | null>(null);
  return vendor ? <><section className="card"><h2>Selected supplier SKU</h2><p><strong>{vendor.vendor_name}</strong> · <span className="mono">{vendor.vendor_sku}</span> · <StatusBadge value={vendor.mapping_status} /></p><button type="button" className="button" onClick={() => setVendor(null)}>Choose a different supplier SKU</button></section>
    <PolicyEditor vendorId={vendor.vendor_id} productId={productId} vendorProductId={vendor.id} onSaved={policy => router.push("/purchasing-policies?policy=" + encodeURIComponent(policy.policy_id))} onCancel={() => setVendor(null)} />
  </> : <VendorChoice productId={productId} onSelect={setVendor} />;
}
function PolicyDetail({ id }: { id: string }) {
  const { hasRole } = useAuth();
  const query = usePolicy(id);
  const [historyPage, setHistoryPage] = useState(1);
  const history = usePolicyHistory(id, historyPage);
  const [reviewSession, setReviewSession] = useState(0);
  const [selection, setSelection] = useState<PolicyRevision | null>(null);
  const [editor, setEditor] = useState<{ source: PolicyRevision; head: PolicyRevision } | null>(null);
  const policy = query.data;
  const listings = usePolicyListings(policy?.head.product_id ?? "", policy?.head.marketplace_id ?? "");
  const selected = selection ?? policy?.head;
  function edit(source: PolicyRevision) { if (policy) { setSelection(null); setEditor({ source, head: policy.head }); } }
  function saved() { setEditor(null); setSelection(null); setHistoryPage(1); }
  return <><p><Link href="/purchasing-policies">All purchasing policies</Link></p><ErrorNote error={query.error} />{query.isPending ? <Loading what="Loading policy" /> : null}
    {policy ? <>
      <PageHeader title={policy.identity.catalog_item_number + " · " + policy.identity.product_name} lede={policy.identity.vendor_name + " · Supplier SKU " + policy.identity.vendor_sku} actions={<button type="button" className="button" disabled={query.isFetching || Boolean(editor)} onClick={() => { setSelection(null); setReviewSession(value => value + 1); void query.refetch(); }}>Reload policy</button>} />
      <Note>Policy approval records reviewed assumptions. It does not certify current inventory or authorize a purchase.</Note>
      {policy.approved ? <p><Link className="button" href={"/replenishment-recommendations?policy=" + encodeURIComponent(id)}>Evaluate approved policy</Link></p> : null}
      <div className="grid grid--2 policy-statuses">
        <section className="card" data-testid="policy-editing-head"><h2>Editing head</h2><p>Version {policy.head.revision_number} · <StatusBadge value={policy.head.kind} /></p><p className="muted small">{fmtDate(policy.head.recorded_at)}</p><p>{policy.head.reason}</p></section>
        <section className="card" data-testid="policy-current-approval"><h2>Current approval</h2>{policy.approved ? <><p>Version {policy.approved.revision_number} · <StatusBadge value="APPROVED" /></p><p className="muted small">Later drafts leave this approval in place.</p><button type="button" className="button" disabled={Boolean(editor)} onClick={() => setSelection(policy.approved)}>Inspect approved definition</button></> : <p>No current approval.</p>}</section>
      </div>
      {editor ? <><p role="status">Drafting from version {editor.source.revision_number}, against editing head {editor.head.revision_number}.</p><PolicyEditor key={editor.head.id + ":" + editor.source.id} vendorId={policy.identity.vendor_id} productId={policy.head.product_id} vendorProductId={policy.head.vendor_product_id} initial={editor.source.definition} expectedHead={{ policyId: id, revisionId: editor.head.id, number: editor.head.revision_number }} onSaved={saved} onCancel={() => setEditor(null)} /></> : <>
        <section className="card" data-testid="policy-selected-definition"><div className="policy-actions"><h2>{selection ? "Selected historical version " + selection.revision_number : "Latest saved definition · Version " + policy.head.revision_number}</h2>{hasRole("DATA_OPERATOR", "PURCHASING_MANAGER") && selected ? <button type="button" className="button" onClick={() => edit(selected)}>Draft from this version</button> : null}</div>
          {selection ? <><Note tone="warn">You are inspecting a recorded version. Manager actions below always target the latest saved draft or current approval.</Note><button type="button" className="button" onClick={() => setSelection(null)}>Show latest definition</button></> : null}
          {selected ? <><KeyValues rows={[["Entry", <StatusBadge key="kind" value={selected.kind} />], ["Recorded", fmtDate(selected.recorded_at)], ["Reason", selected.reason]]} /><DefinitionView definition={selected.definition} listings={[...(selected.review_context ? reviewListings(selected.review_context) : []), ...(listings.data ?? [])]} />{selected.review_context ? <MappingEvidence context={selected.review_context} /> : null}<details><summary>Version reference</summary><KeyValues rows={[["Version ID", selected.id], ["Recorded by", selected.recorded_by_user_id], ["Definition verification", selected.definition_sha256]]} /></details></> : null}
        </section>
        {hasRole("PURCHASING_MANAGER") ? <PolicyReviewPanel key={id + ":" + (selection?.id ?? "latest") + ":" + reviewSession} policy={policy} listings={listings.data ?? []} /> : null}
      </>}
      <section className="card" data-testid="policy-revision-history"><h2>Version history</h2><ErrorNote error={history.error} />{history.isPending ? <Loading what="Loading policy versions" /> : null}
        {history.data ? <><div className="table-wrap" role="region" aria-label="Policy version history table" tabIndex={0}><table className="table"><thead><tr><th>Version</th><th>Entry</th><th>Recorded</th><th>Reason</th><th /></tr></thead><tbody>{history.data.items.map(version => <tr key={version.id}><td>{version.revision_number}{version.id === policy.head.id ? " · latest" : ""}</td><td><StatusBadge value={version.kind} /></td><td>{fmtDate(version.recorded_at)}</td><td>{version.reason}</td><td><button className="button" type="button" disabled={Boolean(editor)} onClick={() => setSelection(version)}>Inspect version {version.revision_number}</button></td></tr>)}</tbody></table></div><Pager page={history.data.page} pageSize={history.data.page_size} total={history.data.total} onPage={setHistoryPage} /></> : null}
      </section>
    </> : null}
  </>;
}
