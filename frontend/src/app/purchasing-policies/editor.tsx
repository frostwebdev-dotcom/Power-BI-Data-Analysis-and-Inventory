"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cloneElement, useId, useState } from "react";
import type { FormEvent, ReactElement } from "react";

import { EmptyState, ErrorNote, Loading, Note, Pager, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { buildPolicy, policyError, policyForm, policyPath, US_MARKETPLACE, usePolicyListings, useSupplierProfiles, useVendorProductChoices } from "@/lib/purchasing-policies";
import type { PolicyDefinition, PolicyForm, PolicySummary, PoolForm, UnitForm, VendorProductChoice } from "@/lib/purchasing-policies";

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactElement<{ id?: string; "aria-describedby"?: string }> }) {
  const id = useId();
  return <div className="field"><label className="field__label" htmlFor={id}>{label}</label>{cloneElement(children, { id, "aria-describedby": hint ? id + "-hint" : undefined })}{hint ? <small id={id + "-hint"}>{hint}</small> : null}</div>;
}
export function VendorChoice({ productId, onSelect }: { productId: string; onSelect: (choice: VendorProductChoice) => void }) {
  const [text, setText] = useState(""), [search, setSearch] = useState(""), [page, setPage] = useState(1);
  const query = useVendorProductChoices(productId, page, search);
  return <section className="card" aria-label="Choose a supplier SKU">
    <h2>Choose a supplier SKU</h2><p className="muted">These supplier lines are already linked to the selected product. Approval still checks their mapping evidence.</p>
    <form className="toolbar" onSubmit={e => { e.preventDefault(); setSearch(text.trim()); setPage(1); }}><Field label="Find a supplier or SKU"><input maxLength={200} value={text} onChange={e => setText(e.target.value)} /></Field><button type="submit" className="button">Search supplier SKUs</button></form>
    <ErrorNote error={query.error} />{query.isPending ? <Loading what="Loading supplier SKUs" /> : null}
    {query.data?.items.map(choice => <div className="candidate" key={choice.id}>
      <div><strong>{choice.vendor_name}</strong> · <span className="mono">{choice.vendor_sku}</span><div className="small muted">{choice.vendor_code} · <StatusBadge value={choice.mapping_status} />{!choice.is_active || !choice.vendor_is_active ? " · inactive" : ""}</div></div>
      <button type="button" className="button" onClick={() => onSelect(choice)}>Use SKU {choice.vendor_sku}</button>
    </div>)}
    {query.data?.total === 0 ? <EmptyState title="No supplier SKU found" text="Choose another search or resolve this product’s supplier mapping in the exception queue first." /> : null}
    {query.data ? <Pager page={query.data.page} pageSize={query.data.page_size} total={query.data.total} onPage={setPage} /> : null}
  </section>;
}

export function PolicyEditor({ vendorId, productId, vendorProductId, initial, expectedHead, onSaved, onCancel }: {
  vendorId: string; productId: string; vendorProductId: string; initial?: PolicyDefinition; expectedHead?: { policyId: string; revisionId: string; number: number }; onSaved: (policy: PolicySummary) => void; onCancel: () => void;
}) {
  const cache = useQueryClient();
  const [form, setForm] = useState<PolicyForm>(() => policyForm(initial));
  const [reason, setReason] = useState(""), [error, setError] = useState<Error | null>(null);
  const [excluded, setExcluded] = useState<string[]>([]);
  const listings = usePolicyListings(productId, form.marketplace);
  const profiles = useSupplierProfiles(vendorId, form.supplierEnabled);
  const names = new Map((listings.data ?? []).map(l => [l.id, l.seller_sku]));
  const allIds = [...new Set([...(listings.data ?? []).map(l => l.id), ...form.units.map(u => u.listing_id)])].filter(id => !excluded.includes(id));
  const units = allIds.map(id => form.units.find(u => u.listing_id === id) ?? { listing_id: id, factor: "", reference: "" });
  function field<K extends keyof PolicyForm>(key: K, value: PolicyForm[K]) { setForm(old => ({ ...old, [key]: value })); setError(null); }
  function changeUnit(id: string, key: "factor" | "reference", value: string) {
    const current = units.find(u => u.listing_id === id)!;
    field("units", [...form.units.filter(u => u.listing_id !== id), { ...current, [key]: value }]);
  }
  function changePool(key: string, changes: Partial<PoolForm>) { field("pools", form.pools.map(p => p.key === key ? { ...p, ...changes } : p)); }
  const save = useMutation({
    mutationFn: (definition: PolicyDefinition) => expectedHead
      ? api.post<PolicySummary>(policyPath(expectedHead.policyId) + "/revisions", { expected_head_id: expectedHead.revisionId, definition, reason: reason.trim() })
      : api.post<PolicySummary>("/purchasing-policies", { vendor_product_id: vendorProductId, definition, reason: reason.trim() }),
    onSuccess: policy => { cache.setQueryData(["purchasing-policies", policy.policy_id], policy); void cache.invalidateQueries({ queryKey: ["purchasing-policies"] }); onSaved(policy); },
    onError: issue => { setError(policyError(issue)); void cache.invalidateQueries({ queryKey: ["purchasing-policies"] }); },
  });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null);
    try {
      if (!listings.data || listings.isFetching || listings.isError) throw new Error("Load the complete Amazon listing set before saving.");
      if (reason.trim().length < 10 || reason.trim().length > 1000) throw new Error("Explain the draft in 10–1000 characters.");
      if (form.supplierEnabled && (!profiles.data || profiles.isFetching || profiles.isError)) throw new Error("Load the complete supplier profile list before saving.");
      save.mutate(buildPolicy(form, productId, units));
    } catch (issue) { setError(policyError(issue)); }
  }
  const numeric = (key: "target" | "lead" | "safety" | "minimum" | "multiple" | "ordersAge" | "inventoryAge", label: string, hint: string) => <Field label={label} hint={hint}><input inputMode="numeric" pattern="[0-9]+" required value={form[key]} onChange={e => field(key, e.target.value)} /></Field>;
  const ref = (label: string, value: string, onChange: (value: string) => void) => <Field label={label} hint="10–500 characters, or blank while unconfirmed."><textarea minLength={10} maxLength={500} rows={2} value={value} onChange={e => onChange(e.target.value)} /></Field>;
  return <section className="card policy-editor" data-testid="policy-editor">
    <h2>{expectedHead ? "Edit a new draft" : "Define the policy"}</h2>
    <p className="muted">{expectedHead ? `Saving appends to head version ${expectedHead.number}; the current approval remains in place.` : "Enter the agreed values explicitly. A saved draft does not approve its assumptions."}</p>
    <form onSubmit={submit}><fieldset disabled={save.isPending}>
      <h3>Demand and coverage</h3><div className="grid grid--2">
        <Field label="Marketplace"><select required value={form.marketplace} onChange={e => field("marketplace", e.target.value)}><option value="">Choose marketplace</option><option value={US_MARKETPLACE}>Amazon US · Amazon.com</option></select></Field>
        <Field label="Demand basis"><select required value={form.demand} onChange={e => field("demand", e.target.value)}><option value="">Choose demand basis</option><option value="FULLY_SHIPPED_ONLY">Fully shipped orders only</option></select></Field>
        <Field label="Demand window"><select required value={form.window} onChange={e => field("window", e.target.value)}><option value="">Choose window</option>{[7, 14, 30].map(n => <option key={n} value={n}>{n} days</option>)}</select></Field>
        {numeric("target", "Target coverage days", "1–3650 days; excludes the separately added lead and safety days.")}
        {numeric("lead", "Lead time days", "0–3650 days. Enter zero only if confirmed.")}
        {numeric("safety", "Safety days", "0–3650 days. Enter zero only if confirmed.")}
        <Field label="Coverage interpretation"><select required value={form.coverage} onChange={e => field("coverage", e.target.value)}><option value="">Choose interpretation</option><option value="target-plus-lead-plus-safety">Target + lead time + safety</option></select></Field>
        {ref("Coverage confirmation", form.coverageReference, value => field("coverageReference", value))}
      </div>
      <h3>Supplier ordering units</h3><div className="grid grid--2">
        {numeric("minimum", "Minimum order (base units)", "0–1,000,000,000 base units. Enter zero only if there is no confirmed minimum.")}
        {numeric("multiple", "Order multiple (base units)", "1–1,000,000,000 base units; must contain whole supplier order units.")}
        <Field label="Base units per supplier order unit" hint="Blank means unknown. Do not infer this from the product’s pack size."><input inputMode="numeric" pattern="[0-9]+" value={form.vendorFactor} onChange={e => field("vendorFactor", e.target.value)} /></Field>
        {ref("Supplier unit evidence", form.vendorReference, value => field("vendorReference", value))}
      </div>
      <h3>Supplier availability checks</h3>
      <label className="check"><input type="checkbox" checked={form.supplierEnabled} onChange={e => field("supplierEnabled", e.target.checked)} />Include supplier source checks</label>
      <p className="small muted">Choose the source layout and confirm its stock units. Receipt age measures when the file reached PRMS; it does not prove when the supplier prepared its data.</p>
      {form.supplierEnabled ? <>
        <ErrorNote error={profiles.error} />{profiles.isPending ? <Loading what="Loading supplier inventory profiles" /> : null}
        <div className="grid grid--2">
          <Field label="Supplier inventory profile"><select value={form.supplierProfile} onChange={e => field("supplierProfile", e.target.value)}><option value="">Unconfirmed profile</option>{profiles.data?.map(profile => <option key={profile.id} value={profile.id}>{profile.name} · version {profile.version}{profile.is_active ? "" : " · inactive"}</option>)}{form.supplierProfile && profiles.data && !profiles.data.some(p => p.id === form.supplierProfile) ? <option value={form.supplierProfile}>Previously selected profile · unavailable</option> : null}</select></Field>
          <Field label="Maximum supplier file receipt age (seconds)" hint="1–2,678,400 seconds since PRMS received the file."><input required inputMode="numeric" pattern="[0-9]+" value={form.supplierAge} onChange={e => field("supplierAge", e.target.value)} /></Field>
          <Field label="Supplier freshness basis"><select required value={form.supplierBasis} onChange={e => field("supplierBasis", e.target.value)}><option value="">Choose freshness basis</option><option value="FILE_RECEIPT_TIME">File receipt time · supplier data time unverified</option></select></Field>
          {ref("Supplier receipt-age confirmation", form.supplierFreshnessReference, value => field("supplierFreshnessReference", value))}
          <Field label="Base units per source quantity" hint="Explicit conversion of the file’s as-stated stock count. Blank means unknown; pack size is not copied."><input inputMode="numeric" pattern="[0-9]+" value={form.supplierFactor} onChange={e => field("supplierFactor", e.target.value)} /></Field>
          {ref("Source quantity evidence", form.supplierQuantityReference, value => field("supplierQuantityReference", value))}
          {ref("Supplier availability evidence", form.supplierAvailabilityReference, value => field("supplierAvailabilityReference", value))}
        </div>
      </> : <p className="small muted">Supplier availability remains unverified without these settings.</p>}
      <h3>Freshness and inbound</h3><div className="grid grid--2">
        {numeric("ordersAge", "Maximum sales-data lag (seconds)", "1–2,678,400 seconds from the complete sales-window cutoff.")}
        {numeric("inventoryAge", "Maximum inventory age (seconds)", "1–2,678,400 seconds; applied to the run and each stock snapshot.")}
        <Field label="Inbound handling" hint="This selects the policy; it does not confirm any shipment’s arrival."><select required value={form.inbound} onChange={e => field("inbound", e.target.value)}><option value="">Choose inbound handling</option><option value="CONFIRMED_BEFORE_STOCKOUT_ONLY">Only arrivals confirmed before stockout</option></select></Field>
      </div>
      <h3>Amazon listing units</h3><p className="muted small">Conversions and evidence can stay blank while unknown. Every listing must be confirmed before approval.</p>
      {form.marketplace ? <><ErrorNote error={listings.error} />{listings.isPending ? <Loading what="Loading all Amazon US listings" /> : null}</> : <Note>Choose the marketplace to load its listings.</Note>}
      {listings.data?.length === 0 && units.length === 0 ? <Note tone="warn">No known Amazon US listings. You can retain a draft; approval stays blocked.</Note> : null}
      {units.map((unit: UnitForm) => { const listing = listings.data?.find(l => l.id === unit.listing_id); return <div className="policy-subcard" key={unit.listing_id}>
        <h4>{listing?.seller_sku ?? "Previously referenced listing"}</h4><p className="muted small">{listing ? <>{listing.asin ?? "ASIN unknown"} · <StatusBadge value={listing.mapping_status} />{!listing.is_active ? " · inactive" : ""}</> : "This listing is no longer in the current product’s listing set. Check pool membership before removing it."}</p>
        {!listing ? <button type="button" className="button" onClick={() => setExcluded(old => [...old, unit.listing_id])}>Remove obsolete listing</button> : null}
        <div className="grid grid--2"><Field label={"Base units per listing unit · " + (listing?.seller_sku ?? unit.listing_id)} hint="Positive whole number, or blank while unknown."><input inputMode="numeric" pattern="[0-9]+" value={unit.factor} onChange={e => changeUnit(unit.listing_id, "factor", e.target.value)} /></Field>
        {ref("Conversion evidence · " + (listing?.seller_sku ?? unit.listing_id), unit.reference, value => changeUnit(unit.listing_id, "reference", value))}</div>
      </div>; })}
      <h3>Physical stock pools</h3><p className="muted small">Put shared-stock aliases in the same pool. Choose one authoritative listing for stock; listing quantities are never added together here.</p>
      {form.pools.length === 0 ? <Note tone="warn">No stock pools defined. Approval will require their membership and evidence.</Note> : null}
      {form.pools.map((pool, index) => <div className="policy-subcard" key={pool.key} role="group" aria-label={"Stock pool " + (index + 1)}>
        <div className="policy-actions"><h4>Stock pool {index + 1}</h4><button type="button" className="button button--ghost" onClick={() => field("pools", form.pools.filter(p => p.key !== pool.key))}>Remove pool {index + 1}</button></div>
        <div className="grid grid--2"><Field label={"Pool name · " + (index + 1)} hint="Unique name, e.g. main-warehouse. Letters, numbers, dot, dash or underscore."><input required maxLength={64} pattern="[A-Za-z0-9._-]+" value={pool.name} onChange={e => changePool(pool.key, { name: e.target.value })} /></Field>
        <Field label={"Fulfillment channel · " + (index + 1)}><select required value={pool.channel} onChange={e => changePool(pool.key, { channel: e.target.value })}><option value="">Choose channel</option><option value="FBA">FBA · Amazon fulfilled</option><option value="FBM">FBM · Merchant fulfilled</option></select></Field></div>
        <details open><summary>Member listings</summary><div className="policy-members">{[...new Set([...allIds, ...pool.members])].map(id => <label className="check" key={id}><input type="checkbox" checked={pool.members.includes(id)} onChange={e => changePool(pool.key, { members: e.target.checked ? [...pool.members, id] : pool.members.filter(m => m !== id), source: !e.target.checked && pool.source === id ? "" : pool.source })} />{names.get(id) ?? "Historical listing " + id}</label>)}</div></details>
        <div className="grid grid--2"><Field label={"Authoritative stock listing · " + (index + 1)}><select required value={pool.source} onChange={e => changePool(pool.key, { source: e.target.value })}><option value="">Choose one member as stock source</option>{pool.members.map(id => <option key={id} value={id}>{names.get(id) ?? "Historical listing " + id}</option>)}</select></Field>
        {ref("Distinct stock evidence · " + (index + 1), pool.reference, value => changePool(pool.key, { reference: value }))}</div>
      </div>)}
      <button type="button" className="button" disabled={form.pools.length >= 200} onClick={() => field("pools", [...form.pools, { key: crypto.randomUUID(), name: "", channel: "", members: [], source: "", reference: "" }])}>Add stock pool</button>
      <Field label="Reason for this draft" hint="10–1000 characters. Explain the source or intended change."><textarea required minLength={10} maxLength={1000} rows={3} value={reason} onChange={e => setReason(e.target.value)} /></Field>
      <ErrorNote error={error} />
      <div className="policy-actions"><button className="button button--primary" type="submit" disabled={!listings.data || listings.isFetching || listings.isError || reason.trim().length < 10}>{save.isPending ? "Saving…" : "Save draft"}</button><button className="button" type="button" onClick={onCancel}>Cancel editing</button></div>
    </fieldset></form>
  </section>;
}
