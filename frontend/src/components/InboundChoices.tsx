"use client";
import Link from "next/link";
import { useState } from "react";
import { ErrorNote, Loading, Note, Pager, fmtDate } from "./ui";
import { useInbound } from "@/lib/inbound";
import { quantity } from "@/lib/recommendations";
export function InboundChoices({ policy, approval, value, onChange, disabled = false }: { policy: string; approval: string; value: string[]; onChange: (ids: string[]) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false), [page, setPage] = useState(1);
  const query = useInbound(policy, page, open);
  return <details className="card" open={open} onToggle={e => setOpen(e.currentTarget.open)}><summary>Optional reviewed inbound arrivals · {value.length} selected</summary>
    <Note>Explicitly select current complete-position confirmations. Freshness, snapshot identity and arrival timing are rechecked by the server. Leaving evidence unselected preserves native unknowns.</Note>
    <p><Link href={"/inbound-evidence?policy=" + encodeURIComponent(policy)}>Review or record inbound evidence</Link></p>
    <button className="button" disabled={disabled} onClick={() => { onChange([]); void query.refetch(); }}>Reload inbound choices and clear selection</button>
    <ErrorNote error={query.error} />{query.isPending ? <Loading /> : null}
    {query.data?.items.map(row => <label className="checkbox-row" key={row.id}><input type="checkbox" checked={value.includes(row.id)} disabled={disabled || row.kind !== "CONFIRMED" || row.approved_revision_id !== approval} onChange={e => onChange(e.target.checked ? [...value, row.id] : value.filter(id => id !== row.id))} />{row.pool_id} · version {row.revision_number} · {row.kind.toLowerCase()} · {quantity(row.position.declared_total_base_units)} base units · expires {fmtDate(row.position.expires_at)}</label>)}
    {query.data?.total === 0 ? <p>No inbound confirmations recorded for this policy.</p> : null}
    {value.length ? <details><summary>Selected revision references</summary><ul>{value.map(id => <li key={id}><span className="mono small">{id}</span> <button className="button" disabled={disabled} onClick={() => onChange(value.filter(v => v !== id))}>Remove revision {id}</button></li>)}</ul></details> : <p>No reviewed inbound selected.</p>}
    {query.data ? <Pager page={page} pageSize={query.data.page_size} total={query.data.total} onPage={p => { if (!disabled) setPage(p); }} /> : null}
  </details>;
}
