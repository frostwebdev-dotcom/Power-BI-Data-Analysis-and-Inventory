"use client";

import { useState } from "react";

import { Drawer, EmptyState, ErrorNote, KeyValues, Loading, PageHeader, Pager, fmtDate } from "@/components/ui";
import { useAudit } from "@/lib/queries";
import type { AuditEvent } from "@/lib/types";

export default function AuditPage() {
  const [page, setPage] = useState(1);
  const [entityType, setEntityType] = useState("");
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [entityId, setEntityId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const audit = useAudit({
    page,
    page_size: 50,
    entity_type: entityType,
    action,
    actor,
    entity_id: entityId.trim(),
    occurred_from: from ? new Date(from).toISOString() : "",
    occurred_to: to ? new Date(to).toISOString() : "",
  });

  return (
    <>
      <PageHeader title="Audit log" lede="Every important change, who made it, and the state before and after. Rows are append-only; nothing here can be edited or removed." />
      <div className="toolbar toolbar--wrap">
        <select value={entityType} onChange={(e) => { setEntityType(e.target.value); setPage(1); }} aria-label="Entity">
          <option value="">Any entity</option>
          {(audit.data?.entity_types ?? []).map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} aria-label="Action">
          <option value="">Any action</option>
          {(audit.data?.actions ?? []).map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <input type="search" placeholder="Actor email" value={actor} onChange={(e) => { setActor(e.target.value); setPage(1); }} aria-label="Actor" />
        <input type="search" placeholder="Entity id" className="mono" value={entityId} onChange={(e) => { setEntityId(e.target.value); setPage(1); }} aria-label="Entity id" />
        <input type="datetime-local" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1); }} aria-label="From" />
        <input type="datetime-local" value={to} onChange={(e) => { setTo(e.target.value); setPage(1); }} aria-label="To" />
      </div>
      <ErrorNote error={audit.error} />
      {audit.isPending ? <Loading what="Loading the audit log" /> : null}
      {audit.data && audit.data.items.length === 0 ? <EmptyState title="No audit entries match" /> : null}
      {audit.data && audit.data.items.length > 0 ? (
        <div className="table-wrap">
          <table className="table" data-testid="audit-events">
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Entity</th>
                <th>Summary</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {audit.data.items.map((event) => (
                <tr key={event.id}>
                  <td>{fmtDate(event.occurred_at)}</td>
                  <td>{event.actor_label ?? event.actor_type.toLowerCase()}</td>
                  <td className="mono">{event.action}</td>
                  <td>
                    {event.entity_type}
                    {event.entity_id ? <span className="mono muted small"> {event.entity_id.slice(0, 8)}…</span> : null}
                  </td>
                  <td>{event.summary ?? "—"}</td>
                  <td className="actions">
                    <button type="button" className="button button--ghost" onClick={() => setSelected(event)}>
                      Diff
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager page={audit.data.page} pageSize={audit.data.page_size} total={audit.data.total} onPage={setPage} />
        </div>
      ) : null}
      {selected ? <DiffDrawer event={selected} onClose={() => setSelected(null)} /> : null}
    </>
  );
}

function DiffDrawer({ event, onClose }: { event: AuditEvent; onClose: () => void }) {
  const before = event.before ?? {};
  const after = event.after ?? {};
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
  const changed = new Set(event.changed_fields ?? keys.filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k])));
  const render = (value: unknown) => (value === undefined ? <span className="muted">—</span> : <span className="mono">{JSON.stringify(value)}</span>);

  return (
    <Drawer title={event.action} onClose={onClose} wide>
      <KeyValues
        rows={[
          ["When", fmtDate(event.occurred_at)],
          ["Actor", `${event.actor_label ?? "—"} (${event.actor_type.toLowerCase()})`],
          ["Entity", `${event.entity_type} ${event.entity_id ?? ""}`],
          ["Request", <span key="r" className="mono small">{event.request_id ?? "—"}</span>],
          ["Summary", event.summary ?? "—"],
        ]}
      />
      <table className="table table--compact diff" data-testid="audit-diff">
        <thead>
          <tr>
            <th>Field</th>
            <th>Before</th>
            <th>After</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key} className={changed.has(key) ? "diff__changed" : undefined}>
              <td>{key}</td>
              <td>{render(before[key])}</td>
              <td>{render(after[key])}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {keys.length === 0 ? <p className="muted">No state captured for this entry.</p> : null}
    </Drawer>
  );
}
