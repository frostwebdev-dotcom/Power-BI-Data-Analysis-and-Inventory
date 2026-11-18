"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ErrorNote, KeyValues, Loading, Note, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { inboundPath, useInbound } from "@/lib/inbound";
import type { InboundContext, InboundPosition, InboundPreview, InboundProposal, InboundRecord, InboundRevision, InboundRevoke } from "@/lib/inbound";
import { usePolicies, usePolicy } from "@/lib/purchasing-policies";
import { exactDisplay, hasUnsafeQuantity, quantity } from "@/lib/recommendations";
import { Reasons } from "../replenishment-recommendations/evidence";

export default function InboundPage() { return <Suspense fallback={<Loading />}><Inbound /></Suspense>; }
function Inbound() {
  const params = useSearchParams(), policy = params.get("policy");
  return <div className="recommendations inbound-evidence"><PageHeader title="Reviewed inbound arrivals" lede="Record a complete outstanding shipment position against an exact approved stock pool and inventory snapshot." /><Note>Review evidence only. Recording does not receive stock, update Amazon, reserve inventory or authorize a purchase. Unknown quantities or dates cannot be confirmed as zero.</Note>{policy ? <PolicyEvidence key={policy} policy={policy} /> : <ChoosePolicy />}</div>;
}
function ChoosePolicy() {
  const [page, setPage] = useState(1), policies = usePolicies(page, "");
  return <section className="card"><h2>Choose an approved purchasing policy</h2><ErrorNote error={policies.error} />{policies.isPending ? <Loading /> : null}{policies.data?.items.map(p => <div className="candidate" key={p.policy_id}><div>{p.identity.catalog_item_number} · {p.identity.vendor_name} · {p.identity.vendor_sku}</div>{p.approved ? <Link className="button" href={"/inbound-evidence?policy=" + encodeURIComponent(p.policy_id)}>Review inbound for {p.identity.catalog_item_number}</Link> : <span>No current approval</span>}</div>)}{policies.data?.total === 0 ? <p>No policies recorded.</p> : null}{policies.data ? <Pager page={page} pageSize={policies.data.page_size} total={policies.data.total} onPage={setPage} /> : null}</section>;
}
function PolicyEvidence({ policy }: { policy: string }) {
  const query = usePolicy(policy), { hasRole } = useAuth(), cache = useQueryClient();
  const [pool, setPool] = useState(""), [epoch, setEpoch] = useState(0), [page, setPage] = useState(1), [history, setHistory] = useState(false), [locked, setLocked] = useState(false);
  const revisions = useInbound(policy, page, true, !history), approval = query.data?.approved;
  async function refresh() { setEpoch(e => e + 1); await Promise.all([query.refetch(), cache.invalidateQueries({ queryKey: ["inbound", policy] })]); }
  return <><p><Link href="/inbound-evidence">Choose another policy</Link> · <Link href={"/replenishment-recommendations?policy=" + encodeURIComponent(policy)}>Evaluate this policy</Link></p><ErrorNote error={query.error} />{query.isPending ? <Loading /> : null}
    {query.data ? <section className="card"><h2>{query.data.identity.catalog_item_number} · {query.data.identity.vendor_name}</h2><p>{query.data.identity.vendor_sku} · {approval ? "approved version " + approval.revision_number : "No current approval"}</p><Link href={"/purchasing-policies?policy=" + encodeURIComponent(policy)}>Review approved assumptions</Link>
      {approval ? <label className="field"><span className="field__label">Stock pool to review</span><select value={pool} disabled={locked} onChange={e => setPool(e.target.value)}><option value="">Choose a stock pool</option>{approval.definition.amazon.stock_pools.map(p => <option key={p.pool_id} value={p.pool_id}>{p.pool_id} · {p.channel}</option>)}</select></label> : null}
    </section> : null}
    {approval && pool ? <ReviewPosition key={approval.id + pool + epoch} policy={policy} approval={approval.id} pool={pool} canManage={hasRole("PURCHASING_MANAGER")} disabled={locked} onLock={setLocked} onDone={() => void refresh()} /> : null}
    <section className="card"><h2>Inbound evidence ledger</h2><label className="checkbox-row"><input type="checkbox" checked={history} disabled={locked} onChange={e => { setHistory(e.target.checked); setPage(1); }} />Show complete revision history</label><button className="button" disabled={locked} onClick={() => void refresh()}>Reload policy and inbound ledger</button><ErrorNote error={revisions.error} />{revisions.isPending ? <Loading /> : null}{revisions.data?.total === 0 ? <p>No inbound evidence recorded.</p> : null}
      {revisions.data?.items.map(r => <article className="card" key={r.id}><h3>{r.pool_id} · revision {r.revision_number} · <StatusBadge value={r.kind} /></h3><KeyValues rows={[["Declared base units", quantity(r.position.declared_total_base_units)], ["Observed", fmtDate(r.position.observed_at)], ["Expires", fmtDate(r.position.expires_at)], ["Evidence reference", r.position.evidence_reference], ["Recorded", fmtDate(r.recorded_at)], ["Reviewer", r.recorded_by_user_id], ["Reason", r.reason]]} /><details><summary>Immutable position and source evidence</summary><pre className="json-block">{JSON.stringify(exactDisplay(r), null, 2)}</pre></details>{!history && r.kind === "CONFIRMED" && hasRole("PURCHASING_MANAGER") ? <Revoke key={r.id} revision={r} disabled={locked} onLock={setLocked} onDone={() => void refresh()} /> : null}</article>)}
      {revisions.data ? <Pager page={page} pageSize={revisions.data.page_size} total={revisions.data.total} onPage={p => { if (!locked) setPage(p); }} /> : null}
    </section>
  </>;
}
function ReviewPosition({ policy, approval, pool, canManage, disabled, onLock, onDone }: { policy: string; approval: string; pool: string; canManage: boolean; disabled: boolean; onLock: (value: boolean) => void; onDone: () => void }) {
  const [context, setContext] = useState<InboundContext | null>(null), [pending, setPending] = useState(false), [error, setError] = useState<unknown>(null);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  async function load() {
    setPending(true); setContext(null); setError(null);
    try { const value = await api.post<InboundContext>(inboundPath(policy) + "/context", { expected_approved_revision_id: approval, pool_id: pool }); if (active.current) setContext(value); }
    catch (err) { if (active.current) setError(err); } finally { if (active.current) setPending(false); }
  }
  return <section className="card"><h2>Review complete position · {pool}</h2>{!context ? <button className="button button--primary" disabled={pending || disabled} onClick={() => void load()}>{pending ? "Loading source context…" : "Load current stock source"}</button> : null}<ErrorNote error={error} />
    {context ? <><KeyValues rows={[["Channel", context.channel], ["Stock captured", typeof context.source_context.source_capture_at === "string" ? fmtDate(context.source_context.source_capture_at) : "Unknown"], ["On hand base units", quantity(typeof context.source_context.on_hand_base_units === "number" ? context.source_context.on_hand_base_units : null)], ["Native observed inbound base units", quantity(typeof context.source_context.observed_inbound_base_units === "number" ? context.source_context.observed_inbound_base_units : null)], ["Source digest", context.source_sha256]]} /><Reasons codes={context.blockers} /><details><summary>Stock source context</summary><pre className="json-block">{JSON.stringify(exactDisplay(context.source_context), null, 2)}</pre></details>
      {canManage ? <PositionForm context={context} policy={policy} disabled={disabled} onLock={onLock} onDone={onDone} onStale={() => { setContext(null); setError(new Error("Source, approval or head changed. Reload the policy and source before reviewing again.")); }} /> : <Note>You have read-only access. A purchasing manager must confirm or revoke inbound evidence.</Note>}
    </> : null}
  </section>;
}
interface DraftArrival { reference: string; units: string; eta: string }
function PositionForm({ context, policy, disabled, onLock, onDone, onStale }: { context: InboundContext; policy: string; disabled: boolean; onLock: (value: boolean) => void; onDone: () => void; onStale: () => void }) {
  const [total, setTotal] = useState(""), [observed, setObserved] = useState(""), [expiry, setExpiry] = useState(""), [reference, setReference] = useState(""), [arrivals, setArrivals] = useState<DraftArrival[]>([]);
  const [complete, setComplete] = useState(false), [overlap, setOverlap] = useState(false), [reason, setReason] = useState(""), [ack, setAck] = useState(false), [preview, setPreview] = useState<InboundPreview | null>(null), [attempt, setAttempt] = useState<InboundRecord | null>(null), [pending, setPending] = useState(false), [error, setError] = useState<unknown>(null);
  const generation = useRef(0), active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const locked = Boolean(attempt) || pending || disabled;
  function edit(action: () => void) { generation.current++; setPreview(null); setAck(false); setError(null); action(); }
  function proposal(): InboundProposal {
    const whole = (value: string, positive = false) => { if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < (positive ? 1 : 0)) throw new Error("Enter explicit whole base-unit quantities within the exact display range."); return Number(value); };
    const date = (value: string) => { if (!/(Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) throw new Error("Use ISO date and time with Z or an explicit timezone offset."); return value; };
    const position: InboundPosition = { declared_total_base_units: whole(total), observed_at: date(observed), expires_at: date(expiry), evidence_reference: reference.trim(), complete_position_confirmed: complete, no_on_hand_overlap_confirmed: overlap, arrivals: arrivals.map(a => ({ reference: a.reference.trim(), base_units: whole(a.units, true), expected_at: date(a.eta) })) };
    return { expected_approved_revision_id: context.approved_revision_id, pool_id: context.pool_id, expected_head_id: context.head_id, expected_source_sha256: context.source_sha256, position };
  }
  async function check() {
    const ticket = ++generation.current; setPending(true); setPreview(null); setAck(false); setError(null);
    try { const value = await api.post<InboundPreview>(inboundPath(policy) + "/preview", proposal()); if (active.current && ticket === generation.current) setPreview(value); }
    catch (err) { if (active.current && ticket === generation.current) { if (err instanceof ApiError && err.statusCode === 409) onStale(); else setError(err); } }
    finally { if (active.current && ticket === generation.current) setPending(false); }
  }
  async function save() {
    if (!preview) return;
    const body: InboundRecord = attempt ?? { ...preview.proposal, request_id: crypto.randomUUID(), expected_preview_sha256: preview.preview_sha256, reason: reason.trim(), acknowledge_review: true };
    setAttempt(body); setPending(true); setError(null); onLock(true);
    try { await api.post<InboundRevision>(inboundPath(policy), body); onLock(false); onDone(); }
    catch (err) { if (err instanceof ApiError && err.statusCode >= 400 && err.statusCode < 500) { setAttempt(null); onLock(false); if (err.statusCode === 409) onStale(); else setError(err); } else setError(new Error("Confirmation result is unconfirmed. Retry the same confirmation to resolve it without duplicating evidence.")); }
    finally { setPending(false); }
  }
  return <div data-testid="inbound-position-form"><Note>All quantities are whole product base units. For FBA, the complete declared position must equal the native observed total after the approved pack conversion. For FBM, confirm the entire outstanding position manually. Zero requires an explicit total of 0 and no arrival rows.</Note>
    <fieldset disabled={locked}><legend>Complete outstanding position</legend><label className="field"><span className="field__label">Declared total base units</span><input inputMode="numeric" value={total} onChange={e => edit(() => setTotal(e.target.value))} /></label>
      <label className="field"><span className="field__label">Position observed at · ISO with timezone</span><input placeholder="2026-11-18T14:00:00Z" value={observed} onChange={e => edit(() => setObserved(e.target.value))} /></label><label className="field"><span className="field__label">Evidence expires at · ISO with timezone</span><input placeholder="2026-11-19T14:00:00Z" value={expiry} onChange={e => edit(() => setExpiry(e.target.value))} /></label>
      <label className="field"><span className="field__label">Evidence reference</span><textarea maxLength={500} value={reference} onChange={e => edit(() => setReference(e.target.value))} /></label>
      {arrivals.map((a, i) => <fieldset key={i}><legend>Arrival {i + 1}</legend><label className="field"><span className="field__label">Shipment reference {i + 1}</span><input maxLength={100} value={a.reference} onChange={e => edit(() => setArrivals(old => old.map((v, n) => n === i ? { ...v, reference: e.target.value } : v)))} /></label><label className="field"><span className="field__label">Arrival base units {i + 1}</span><input inputMode="numeric" value={a.units} onChange={e => edit(() => setArrivals(old => old.map((v, n) => n === i ? { ...v, units: e.target.value } : v)))} /></label><label className="field"><span className="field__label">Expected arrival {i + 1} · ISO with timezone</span><input value={a.eta} onChange={e => edit(() => setArrivals(old => old.map((v, n) => n === i ? { ...v, eta: e.target.value } : v)))} /></label><button className="button" onClick={() => edit(() => setArrivals(old => old.filter((_, n) => n !== i)))}>Remove arrival {i + 1}</button></fieldset>)}
      <button className="button" disabled={arrivals.length >= 100} onClick={() => edit(() => setArrivals(old => [...old, { reference: "", units: "", eta: "" }]))}>Add arrival</button>
      <label className="checkbox-row"><input type="checkbox" checked={complete} onChange={e => edit(() => setComplete(e.target.checked))} />This is the complete outstanding inbound position for this stock pool</label><label className="checkbox-row"><input type="checkbox" checked={overlap} onChange={e => edit(() => setOverlap(e.target.checked))} />These units are not already included in on-hand stock or another stock pool</label>
    </fieldset><ErrorNote error={error} /><button className="button button--primary" disabled={locked || hasUnsafeQuantity(context)} onClick={() => void check()}>{pending && !attempt ? "Checking position…" : "Preview inbound confirmation"}</button>
    {preview ? <section className="card" data-testid="inbound-confirmation-preview"><h3>Confirmation preview</h3><p>{preview.can_confirm ? "Complete position can be confirmed after review." : "Confirmation blocked. Correct the evidence and preview again."}</p><Reasons codes={preview.blockers} /><pre className="json-block">{JSON.stringify(exactDisplay(preview.proposal.position), null, 2)}</pre><label className="field"><span className="field__label">Confirmation reason</span><textarea disabled={locked} maxLength={1000} value={reason} onChange={e => setReason(e.target.value)} /></label><label className="checkbox-row"><input type="checkbox" disabled={locked} checked={ack} onChange={e => setAck(e.target.checked)} />I reviewed the complete position, source snapshot and arrival evidence</label><button className="button button--primary" disabled={pending || disabled && !attempt || !preview.can_confirm || hasUnsafeQuantity(preview) || !ack || reason.trim().length < 10} onClick={() => void save()}>{pending && attempt ? "Confirming…" : attempt ? "Retry same confirmation" : "Confirm reviewed inbound"}</button></section> : null}
  </div>;
}
function Revoke({ revision, disabled, onLock, onDone }: { revision: InboundRevision; disabled: boolean; onLock: (value: boolean) => void; onDone: () => void }) {
  const [reason, setReason] = useState(""), [ack, setAck] = useState(false), [attempt, setAttempt] = useState<InboundRevoke | null>(null), [pending, setPending] = useState(false), [error, setError] = useState<unknown>(null);
  async function revoke() {
    const body: InboundRevoke = attempt ?? { request_id: crypto.randomUUID(), expected_head_id: revision.id, reason: reason.trim(), acknowledge_review: true };
    setAttempt(body); setPending(true); setError(null); onLock(true);
    try { await api.post<InboundRevision>(inboundPath(revision.policy_id) + "/revoke", body); onLock(false); onDone(); }
    catch (err) { if (err instanceof ApiError && err.statusCode >= 400 && err.statusCode < 500) { setAttempt(null); setAck(false); onLock(false); if (err.statusCode === 409) onDone(); else setError(err); } else setError(new Error("Revocation result is unconfirmed. Retry the same revocation below.")); }
    finally { setPending(false); }
  }
  return <details><summary>Revoke this complete position</summary><Note>Revocation appends a new revision. Saved historical evaluations remain available.</Note><label className="field"><span className="field__label">Revocation reason</span><textarea disabled={disabled || Boolean(attempt)} value={reason} maxLength={1000} onChange={e => setReason(e.target.value)} /></label><label className="checkbox-row"><input type="checkbox" checked={ack} disabled={disabled || Boolean(attempt)} onChange={e => setAck(e.target.checked)} />I reviewed and want to revoke this inbound position</label><ErrorNote error={error} /><button className="button" disabled={pending || disabled && !attempt || !ack || reason.trim().length < 10} onClick={() => void revoke()}>{attempt ? "Retry same revocation" : "Revoke inbound position"}</button></details>;
}
