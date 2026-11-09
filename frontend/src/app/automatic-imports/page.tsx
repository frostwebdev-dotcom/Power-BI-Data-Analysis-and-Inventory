"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";

import { Drawer, EmptyState, ErrorNote, Loading, Note, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { api, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProfiles, useVendors } from "@/lib/queries";

interface SourceInput {
  name: string; vendor_id: string; profile_id: string; sender_addresses: string[];
  subject_contains: string | null; filename_pattern: string; mode: "ATTACHMENT" | "LINK";
  download_hosts: string[]; download_path_prefix: string | null; download_filename: string | null;
  stale_after_hours: number | null; is_active: boolean;
}
interface Source extends SourceInput {
  id: string; vendor_code: string; profile_name: string; profile_version: number;
  starts_at: string; last_received_at: string | null; last_success_at: string | null;
  freshness: string;
}
interface Delivery {
  id: string; source_id: string; filename: string; received_at: string; status: string;
  attempts: number; next_attempt_at: string | null; error_code: string | null;
  import_job_id: string | null;
}
interface Connection {
  provider: "microsoft365" | "gmail";
  enabled: boolean; configured: boolean; mailbox: string | null; poll_minutes: number;
  missing_settings: string[]; last_poll_at: string | null; last_success_at: string | null;
  error_code: string | null;
}

const EMPTY: SourceInput = { name: "", vendor_id: "", profile_id: "", sender_addresses: [],
  subject_contains: null, filename_pattern: "*.xlsx", mode: "ATTACHMENT", download_hosts: [],
  download_path_prefix: null, download_filename: null, stale_after_hours: null, is_active: false };
const FRESHNESS: Record<string, string> = { paused: "Paused", never_received: "Waiting for first file",
  awaiting_import: "File received; import needs attention", current: "Up to date",
  stale: "Update overdue", irregular: "Updates as received" };
const REASONS: Record<string, string> = {
  MAILBOX_ACCESS_DENIED: "Mailbox access needs administrator attention.",
  MAILBOX_NOT_CONFIGURED: "The inventory mailbox has not been connected.",
  GMAIL_AUTHORIZATION_REQUIRED: "The Gmail owner must authorize read-only access again.",
  GMAIL_MAILBOX_MISMATCH: "The authorized Google account does not match the inventory mailbox.",
  GMAIL_READONLY_SCOPE_REQUIRED: "Reconnect Gmail with read-only mail access.",
  GMAIL_CURSOR_EXPIRED: "Gmail history expired. The next check will safely recheck recent email.",
  GMAIL_TEMPORARILY_UNAVAILABLE: "Gmail is temporarily unavailable. Automatic checks will retry.",
  AMBIGUOUS_VENDOR: "More than one vendor rule matched this file. Correct the source rules.",
  MULTIPLE_MATCHING_FILES: "Several files matched. Narrow the filename or download rule.",
  NO_MATCHING_FILE_OR_LINK: "No approved inventory file or download link matched this email.",
  DOWNLOAD_LOGIN_REQUIRED: "This download requires a vendor login. Review the link setup.",
  DOWNLOAD_PAGE_REQUIRES_REVIEW: "The link returned a web page instead of an inventory file.",
  VENDOR_OR_PROFILE_INACTIVE: "The vendor or saved profile version is inactive.",
  SOURCE_ROWS_INVALID: "Source rows failed validation. Previous inventory was kept.",
  NEWER_FILE_ALREADY_APPLIED: "A newer file was already imported; this older update was skipped.",
  OLDER_CONTENT_REAPPEARED: "This file was imported before a newer, different file. Review it.",
  PRIOR_IMPORT_INCOMPLETE: "An earlier import needs to finish before this file can apply.",
  IMPORT_REQUIRES_REVIEW: "Open the import report to review the source file or profile.",
};
function reason(code: string | null) { return code ? REASONS[code] ?? "Check failed or file needs review. Reference: " + code : "—"; }

export default function AutomaticImportsPage() {
  const { hasRole } = useAuth();
  const canWrite = hasRole("PURCHASING_MANAGER");
  const client = useQueryClient();
  const [editing, setEditing] = useState<Source | "new" | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [page, setPage] = useState(1);
  const [notice, setNotice] = useState("");
  const status = useQuery({ queryKey: ["vendor-mail", "status"], queryFn: () => api.get<Connection>("/vendor-mail/status"), refetchInterval: 15_000 });
  const sources = useQuery({ queryKey: ["vendor-mail", "sources"], queryFn: () => api.get<{ items: Source[]; total: number }>("/vendor-mail/sources"), refetchInterval: 15_000 });
  const history = useQuery({ queryKey: ["vendor-mail", "deliveries", sourceId, page], queryFn: () => api.get<Page<Delivery>>("/vendor-mail/deliveries", { source_id: sourceId, page, page_size: 25 }), refetchInterval: 15_000 });
  const poll = useMutation({ mutationFn: () => api.post("/vendor-mail/poll"), onSuccess: () => { setNotice("Mailbox check requested. Results will appear below."); void client.invalidateQueries({ queryKey: ["vendor-mail"] }); } });
  const retry = useMutation({ mutationFn: (input: { id: string; current: boolean }) => api.post(`/vendor-mail/deliveries/${input.id}/retry`, { use_current_profile: input.current }), onSuccess: () => { setNotice("Retry queued for the next mailbox check."); void client.invalidateQueries({ queryKey: ["vendor-mail"] }); } });
  const sourceName = (id: string) => sources.data?.items.find((s) => s.id === id)?.name ?? "Source unavailable";
  const connection = status.data;
  return <>
    <PageHeader title="Automatic imports" lede="Receive vendor inventory from Gmail or Microsoft 365 attachments and approved download links. New files use your saved import profiles; earlier inventory remains when no update arrives."
      actions={canWrite ? <button className="button button--primary" type="button" onClick={() => setEditing("new")}>Add email source</button> : null} />
    <ErrorNote error={status.error ?? sources.error ?? history.error ?? poll.error ?? retry.error} />
    {notice ? <p role="status" className="note note--info">{notice}</p> : null}
    {status.isPending ? <Loading what="Checking mailbox connection" /> : null}
    {connection ? <section className="card">
      <h2>Inventory mailbox</h2>
      {connection.mailbox ? <p className="muted">{connection.provider === "gmail" ? "Gmail" : "Microsoft 365"} · Read-only access</p> : null}
      {!connection.configured ? <Note tone="warn">Mailbox connection required. An administrator must configure read-only access to the inventory mailbox before automatic checks can run. You can prepare vendor sources below.</Note> : !connection.enabled ? <Note tone="warn">Mailbox configured; automatic checks are paused.</Note> : <Note tone="ok">Automatic checks enabled every {connection.poll_minutes} minutes. Files are imported when vendors send them.</Note>}
      <dl className="detail-grid"><div><dt>Mailbox</dt><dd>{connection.mailbox ?? "Not configured"}</dd></div><div><dt>Last check</dt><dd>{fmtDate(connection.last_poll_at)}</dd></div><div><dt>Last successful mailbox check</dt><dd>{fmtDate(connection.last_success_at)}</dd></div></dl>
      {connection.error_code ? <Note tone="warn">{reason(connection.error_code)}</Note> : null}
      {hasRole("ADMIN") ? <button type="button" className="button" disabled={!connection.enabled || !connection.configured || poll.isPending} onClick={() => poll.mutate()}>{poll.isPending ? "Requesting check…" : "Check mailbox now"}</button> : null}
    </section> : null}
    <section className="card">
      <h2>Vendor sources</h2><p className="muted">Use a separate source for each file layout or worksheet. Leave the overdue threshold empty for vendors that send files irregularly.</p>
      {sources.isPending ? <Loading /> : sources.data?.items.length === 0 ? <EmptyState title="No email sources configured" text="Choose a vendor, its approved sender and an existing import profile to prepare automatic updates." /> : <div className="table-wrap"><table className="table"><thead><tr><th>Source / vendor</th><th>Profile</th><th>Status</th><th>Last file received</th><th>Last file successfully processed</th><th /></tr></thead><tbody>
        {sources.data?.items.map((s) => <tr key={s.id}><td><strong>{s.name}</strong><div className="muted">{s.vendor_code} · {s.mode === "LINK" ? "Download link" : "Attachment"}</div></td><td>{s.profile_name} v{s.profile_version}</td><td><span className={`badge ${s.freshness === "stale" || s.freshness === "awaiting_import" ? "badge--warn" : s.freshness === "current" ? "badge--ok" : ""}`}>{FRESHNESS[s.freshness] ?? s.freshness}</span></td><td>{fmtDate(s.last_received_at)}</td><td>{fmtDate(s.last_success_at)}</td><td>{canWrite ? <button type="button" className="button button--ghost" aria-label={`Edit ${s.name}`} onClick={() => setEditing(s)}>Edit</button> : null}</td></tr>)}
      </tbody></table></div>}
    </section>
    <section className="card">
      <h2>Received files and issues</h2><label className="field"><span className="field__label">Filter by source</span><select aria-label="Filter by source" value={sourceId} onChange={(e) => { setSourceId(e.target.value); setPage(1); }}><option value="">All sources</option>{sources.data?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      {history.isPending ? <Loading /> : history.data?.items.length === 0 ? <EmptyState title="No email deliveries yet" text="Configured sources will appear here when matching vendor emails are received." /> : <div className="table-wrap"><table className="table"><thead><tr><th>Received</th><th>Source / file</th><th>Result</th><th>Details</th><th>Import</th><th /></tr></thead><tbody>
        {history.data?.items.map((d) => <tr key={d.id}><td>{fmtDate(d.received_at)}</td><td>{sourceName(d.source_id)}<div className="muted">{d.filename}</div></td><td><StatusBadge value={d.status} /></td><td>{reason(d.error_code)}{d.next_attempt_at ? <div className="muted">Next attempt: {fmtDate(d.next_attempt_at)}</div> : null}</td><td>{d.import_job_id ? <Link href={`/imports?job=${d.import_job_id}`}>View report</Link> : "—"}</td><td>{canWrite && ["FAILED", "NEEDS_REVIEW"].includes(d.status) ? <div className="form__actions"><button type="button" className="button button--ghost" disabled={retry.isPending} onClick={() => retry.mutate({ id: d.id, current: false })}>Retry</button><button type="button" className="button button--ghost" disabled={retry.isPending} onClick={() => retry.mutate({ id: d.id, current: true })}>Retry with source’s current profile</button></div> : null}</td></tr>)}
      </tbody></table></div>}
      {history.data ? <Pager page={page} pageSize={25} total={history.data.total} onPage={setPage} /> : null}
    </section>
    {editing ? <SourceEditor source={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void client.invalidateQueries({ queryKey: ["vendor-mail"] }); }} /> : null}
  </>;
}

function SourceEditor({ source, onClose, onSaved }: { source: Source | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<SourceInput>(source ?? { ...EMPTY });
  const [senders, setSenders] = useState(source?.sender_addresses.join(", ") ?? "");
  const [hosts, setHosts] = useState(source?.download_hosts.join(", ") ?? "");
  const vendors = useVendors({ page_size: 200 });
  const profiles = useProfiles(form.vendor_id || null);
  const set = <K extends keyof SourceInput>(key: K, value: SourceInput[K]) => setForm((old) => ({ ...old, [key]: value }));
  const save = useMutation({ mutationFn: () => {
    const body = { ...form, sender_addresses: senders.split(/[,;\n]/).map((x) => x.trim()).filter(Boolean), download_hosts: hosts.split(/[,;\n]/).map((x) => x.trim()).filter(Boolean) };
    return source ? api.patch(`/vendor-mail/sources/${source.id}`, body) : api.post("/vendor-mail/sources", body);
  }, onSuccess: onSaved });
  function submit(event: FormEvent) { event.preventDefault(); save.mutate(); }
  return <Drawer title={source ? "Edit email source" : "Add email source"} onClose={onClose}>
    <form className="form" onSubmit={submit}>
      <ErrorNote error={save.error ?? vendors.error ?? profiles.error} />
      <label className="field"><span className="field__label">Source name</span><input required maxLength={100} value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Vendor inventory" /></label>
      <label className="field"><span className="field__label">Vendor</span><select aria-label="Vendor" required disabled={Boolean(source)} value={form.vendor_id} onChange={(e) => setForm((old) => ({ ...old, vendor_id: e.target.value, profile_id: "" }))}><option value="">Select vendor</option>{vendors.data?.items.map((v) => <option key={v.id} value={v.id}>{v.code} — {v.name}</option>)}</select></label>
      <label className="field"><span className="field__label">Import profile</span><select aria-label="Import profile" required value={form.profile_id} onChange={(e) => set("profile_id", e.target.value)}><option value="">Select profile</option>{source && !profiles.data?.items.some((p) => p.id === source.profile_id) ? <option value={source.profile_id}>{source.profile_name} v{source.profile_version} — check active version</option> : null}{profiles.data?.items.map((p) => <option key={p.id} value={p.id}>{p.name} v{p.version}</option>)}</select></label>
      <label className="field"><span className="field__label">Approved sender addresses</span><textarea required value={senders} onChange={(e) => setSenders(e.target.value)} placeholder="Exact addresses, separated by commas" /><small>Use the actual vendor sender. Forwarded emails need a rule for the forwarding sender.</small></label>
      <label className="field"><span className="field__label">Subject contains (optional)</span><input maxLength={200} value={form.subject_contains ?? ""} onChange={(e) => set("subject_contains", e.target.value || null)} /></label>
      <label className="field"><span className="field__label">File delivery</span><select value={form.mode} onChange={(e) => set("mode", e.target.value as SourceInput["mode"])}><option value="ATTACHMENT">Email attachment</option><option value="LINK">Approved direct download link</option></select></label>
      {form.mode === "ATTACHMENT" ? <label className="field"><span className="field__label">Attachment filename pattern</span><input required maxLength={200} value={form.filename_pattern} onChange={(e) => set("filename_pattern", e.target.value)} /><small>For example, *.xlsx or Inventory*.csv. Multiple matching files require review.</small></label> : <>
        <Note>Direct file links only. Vendor login pages and unapproved redirects require review. Do not paste signed download URLs or passwords here.</Note>
        <label className="field"><span className="field__label">Approved download hostnames</span><input required value={hosts} onChange={(e) => setHosts(e.target.value)} placeholder="Exact hostnames, separated by commas" /></label>
        <label className="field"><span className="field__label">Download URL path starts with</span><input required value={form.download_path_prefix ?? ""} onChange={(e) => set("download_path_prefix", e.target.value)} placeholder="/inventory/" /></label>
        <label className="field"><span className="field__label">Downloaded filename</span><input required maxLength={200} value={form.download_filename ?? ""} onChange={(e) => set("download_filename", e.target.value)} placeholder="inventory.xlsx" /></label>
      </>}
      <label className="field"><span className="field__label">Mark overdue after hours (optional)</span><input type="number" min={1} max={87600} value={form.stale_after_hours ?? ""} onChange={(e) => set("stale_after_hours", e.target.value ? Number(e.target.value) : null)} /><small>Leave blank for irregular updates. An overdue source keeps its previous inventory.</small></label>
      <label className="check"><input type="checkbox" checked={form.is_active} onChange={(e) => set("is_active", e.target.checked)} />Enable this source for new emails</label>
      <p className="muted">New sources start with future emails. Existing imported files and inventory are preserved.</p>
      <div className="form__actions"><button type="submit" className="button button--primary" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save source"}</button><button type="button" className="button button--ghost" onClick={onClose}>Cancel</button></div>
    </form>
  </Drawer>;
}
