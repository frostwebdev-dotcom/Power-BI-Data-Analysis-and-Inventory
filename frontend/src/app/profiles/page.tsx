"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";

import { SchemaForm, defaultsOf } from "@/components/SchemaForm";
import { Drawer, EmptyState, ErrorNote, Loading, Note, PageHeader, StatusBadge, fmtDate } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useProfiles, useRuleSchemas, useVendors } from "@/lib/queries";
import type { ImportProfile, RuleColumn, ValidationPreview } from "@/lib/types";

const RULE_COLUMNS: RuleColumn[] = [
  "column_map",
  "normalization_rules",
  "availability_rules",
  "quantity_semantics",
  "price_semantics",
  "pack_size_handling",
];

const RULE_TITLES: Record<RuleColumn, string> = {
  column_map: "Column map",
  normalization_rules: "Normalisation",
  availability_rules: "Availability",
  quantity_semantics: "Quantity semantics",
  price_semantics: "Price semantics",
  pack_size_handling: "Pack size",
};

export default function ProfilesPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Profiles />
    </Suspense>
  );
}

function Profiles() {
  const params = useSearchParams();
  const { hasRole } = useAuth();
  const canWrite = hasRole("DATA_OPERATOR");
  const vendors = useVendors({ page_size: 200 });
  const [vendorId, setVendorId] = useState<string>(params.get("vendor") ?? "");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [editing, setEditing] = useState<ImportProfile | "new" | null>(null);

  useEffect(() => {
    if (!vendorId && vendors.data?.items[0]) setVendorId(vendors.data.items[0].id);
  }, [vendorId, vendors.data]);

  const profiles = useProfiles(vendorId || null, includeInactive);

  return (
    <>
      <PageHeader
        title="Import profiles"
        lede="How each vendor's file is read: which column is which, how numbers and UPCs are normalised, what counts as in stock. Editing creates a new version; older versions stay for the imports that used them."
        actions={
          canWrite && vendorId ? (
            <button type="button" className="button button--primary" onClick={() => setEditing("new")}>
              New profile
            </button>
          ) : null
        }
      />
      <div className="toolbar">
        <select value={vendorId} onChange={(event) => setVendorId(event.target.value)} aria-label="Vendor" data-testid="profile-vendor">
          {(vendors.data?.items ?? []).map((vendor) => (
            <option key={vendor.id} value={vendor.id}>
              {vendor.code} — {vendor.name}
            </option>
          ))}
        </select>
        <label className="check">
          <input type="checkbox" checked={includeInactive} onChange={(event) => setIncludeInactive(event.target.checked)} />
          Show retired versions
        </label>
      </div>
      <ErrorNote error={vendors.error ?? profiles.error} />
      {profiles.isPending && vendorId ? <Loading what="Loading profiles" /> : null}
      {profiles.data && profiles.data.items.length === 0 ? (
        <EmptyState title="No profiles for this vendor" text="A profile is needed before a file can be imported." />
      ) : null}
      {profiles.data && profiles.data.items.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Version</th>
                <th>Status</th>
                <th>Format</th>
                <th>Columns</th>
                <th>Header pinned</th>
                <th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {profiles.data.items.map((profile) => (
                <tr key={profile.id} data-testid={`profile-row-${profile.name}`}>
                  <td>{profile.name}</td>
                  <td>v{profile.version}</td>
                  <td>
                    <StatusBadge value={profile.is_active ? "ACTIVE" : "INACTIVE"} />
                  </td>
                  <td>{profile.file_format}</td>
                  <td>{profile.column_map.columns.map((c) => c.target).join(", ")}</td>
                  <td>{profile.header_signature ? "yes" : "no"}</td>
                  <td>{fmtDate(profile.created_at)}</td>
                  <td className="actions">
                    <button type="button" className="button button--ghost" onClick={() => setEditing(profile)}>
                      {canWrite && profile.is_active ? "Edit" : "View"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {editing && vendorId ? (
        <ProfileEditor vendorId={vendorId} profile={editing === "new" ? null : editing} readOnly={!canWrite} onClose={() => setEditing(null)} />
      ) : null}
    </>
  );
}

interface FileSettings {
  name: string;
  file_format: "CSV" | "XLSX";
  encoding: string;
  delimiter: string;
  header_row_index: number;
  skip_rows: number;
  sheet_name: string;
}

type Rules = Record<RuleColumn, Record<string, unknown>>;

function ProfileEditor({
  vendorId,
  profile,
  readOnly,
  onClose,
}: {
  vendorId: string;
  profile: ImportProfile | null;
  readOnly: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const schemas = useRuleSchemas();
  const [settings, setSettings] = useState<FileSettings>({
    name: profile?.name ?? "",
    file_format: profile?.file_format ?? "CSV",
    encoding: profile?.encoding ?? "",
    delimiter: profile?.delimiter ?? "",
    header_row_index: profile?.header_row_index ?? 0,
    skip_rows: profile?.skip_rows ?? 0,
    sheet_name: profile?.sheet_name ?? "",
  });
  const [rules, setRules] = useState<Rules | null>(
    profile
      ? {
          column_map: profile.column_map,
          normalization_rules: profile.normalization_rules,
          availability_rules: profile.availability_rules,
          quantity_semantics: profile.quantity_semantics,
          price_semantics: profile.price_semantics,
          pack_size_handling: profile.pack_size_handling,
        }
      : null,
  );
  const [headerSignature, setHeaderSignature] = useState<string | null>(profile?.header_signature ?? null);
  const [preview, setPreview] = useState<ValidationPreview | null>(null);
  const [sample, setSample] = useState<File | null>(null);

  useEffect(() => {
    if (!rules && schemas.data) {
      const fresh = Object.fromEntries(
        RULE_COLUMNS.map((column) => [column, defaultsOf(schemas.data.schemas[column] ?? {})]),
      ) as Rules;
      fresh.column_map = { columns: [] };
      setRules(fresh);
    }
  }, [rules, schemas.data]);

  const payload = useMemo(
    () => ({
      name: settings.name.trim(),
      file_format: settings.file_format,
      encoding: settings.encoding.trim() || null,
      delimiter: settings.delimiter || null,
      header_row_index: settings.header_row_index,
      skip_rows: settings.skip_rows,
      sheet_name: settings.file_format === "XLSX" && settings.sheet_name ? settings.sheet_name : null,
      ...(rules ?? {}),
      header_signature: headerSignature,
    }),
    [settings, rules, headerSignature],
  );

  const validate = useMutation({
    mutationFn: async () => {
      if (!sample) throw new Error("Choose a sample file first.");
      const form = new FormData();
      form.append("profile", JSON.stringify({ ...payload, header_signature: null }));
      form.append("file", sample);
      return api.upload<ValidationPreview>(`/vendors/${vendorId}/import-profiles/validate`, form);
    },
    onSuccess: (result) => setPreview(result),
  });

  const save = useMutation({
    mutationFn: () => {
      if (profile) {
        const { name: _name, ...changes } = payload;
        void _name;
        return api.patch<ImportProfile>(`/vendors/${vendorId}/import-profiles/${profile.id}`, changes);
      }
      return api.post<ImportProfile>(`/vendors/${vendorId}/import-profiles`, payload);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["profiles"] });
      onClose();
    },
  });

  const deactivate = useMutation({
    mutationFn: () => api.post(`/vendors/${vendorId}/import-profiles/${profile?.id}/deactivate`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["profiles"] });
      onClose();
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!readOnly) save.mutate();
  }

  const disabled = readOnly || (profile ? !profile.is_active : false);

  return (
    <Drawer title={profile ? `${profile.name} v${profile.version}` : "New import profile"} onClose={onClose} wide>
      <form onSubmit={submit} className="form" data-testid="profile-form">
        <fieldset disabled={disabled} className="form__section">
          <legend>File</legend>
          <div className="grid grid--3">
            <label className="field">
              <span className="field__label">Name</span>
              <input name="name" required value={settings.name} disabled={profile !== null} onChange={(e) => setSettings({ ...settings, name: e.target.value })} />
            </label>
            <label className="field">
              <span className="field__label">Format</span>
              <select name="file_format" value={settings.file_format} onChange={(e) => setSettings({ ...settings, file_format: e.target.value as "CSV" | "XLSX" })}>
                <option value="CSV">CSV</option>
                <option value="XLSX">XLSX</option>
              </select>
            </label>
            <label className="field">
              <span className="field__label">Encoding (blank = detect)</span>
              <input name="encoding" value={settings.encoding} onChange={(e) => setSettings({ ...settings, encoding: e.target.value })} />
            </label>
            <label className="field">
              <span className="field__label">Delimiter (blank = sniff)</span>
              <input name="delimiter" value={settings.delimiter} maxLength={4} onChange={(e) => setSettings({ ...settings, delimiter: e.target.value })} />
            </label>
            <label className="field">
              <span className="field__label">Header row index</span>
              <input name="header_row_index" type="number" min={0} value={settings.header_row_index} onChange={(e) => setSettings({ ...settings, header_row_index: Number(e.target.value) })} />
            </label>
            <label className="field">
              <span className="field__label">Skip rows</span>
              <input name="skip_rows" type="number" min={0} value={settings.skip_rows} onChange={(e) => setSettings({ ...settings, skip_rows: Number(e.target.value) })} />
            </label>
            {settings.file_format === "XLSX" ? (
              <label className="field">
                <span className="field__label">Sheet name</span>
                <input name="sheet_name" value={settings.sheet_name} onChange={(e) => setSettings({ ...settings, sheet_name: e.target.value })} />
              </label>
            ) : null}
          </div>
        </fieldset>

        {schemas.isPending || !rules ? <Loading what="Loading rule shapes" /> : null}
        {schemas.data && rules
          ? RULE_COLUMNS.map((column) => (
              <fieldset key={column} disabled={disabled} className="form__section">
                <legend>{RULE_TITLES[column]}</legend>
                <SchemaForm
                  name={column}
                  schema={schemas.data.schemas[column] ?? {}}
                  value={rules[column]}
                  onChange={(next) => setRules({ ...rules, [column]: next })}
                />
              </fieldset>
            ))
          : null}

        <fieldset className="form__section">
          <legend>Validate against a sample file</legend>
          <p className="muted">
            Reads the first 20 rows with these rules and shows what the import would do. Nothing is
            stored. Pinning the header locks this profile to the file&apos;s exact columns (AC-6.4).
          </p>
          <div className="toolbar">
            <input type="file" accept=".csv,.xlsx" data-testid="sample-file" onChange={(e) => setSample(e.target.files?.[0] ?? null)} />
            <button type="button" className="button" disabled={!sample || validate.isPending} onClick={() => validate.mutate()}>
              {validate.isPending ? "Validating…" : "Validate"}
            </button>
            {headerSignature ? (
              <span className="badge badge--ok" title={headerSignature}>
                header pinned
              </span>
            ) : (
              <span className="badge">header not pinned</span>
            )}
          </div>
          <ErrorNote error={validate.error} />
          {preview ? <PreviewTable preview={preview} onPin={() => setHeaderSignature(preview.header_signature)} pinned={headerSignature === preview.header_signature} disabled={disabled} /> : null}
        </fieldset>

        <ErrorNote error={save.error ?? deactivate.error} />
        {!readOnly ? (
          <div className="form__actions">
            {!disabled ? (
              <button type="submit" className="button button--primary" disabled={save.isPending}>
                {profile ? "Save as new version" : "Create profile"}
              </button>
            ) : null}
            {profile?.is_active ? (
              <button type="button" className="button button--danger" disabled={deactivate.isPending} onClick={() => deactivate.mutate()}>
                Deactivate
              </button>
            ) : null}
            {profile && !profile.is_active ? <Note>This version is retired; it is kept for the imports that ran under it.</Note> : null}
          </div>
        ) : null}
      </form>
    </Drawer>
  );
}

function PreviewTable({
  preview,
  onPin,
  pinned,
  disabled,
}: {
  preview: ValidationPreview;
  onPin: () => void;
  pinned: boolean;
  disabled: boolean;
}) {
  const targets = preview.columns.map((c) => c.target).filter((t) => t !== "ignore");
  return (
    <div data-testid="validation-preview">
      <div className="toolbar">
        <StatusBadge value={preview.header_ok ? "OK" : "ERROR"} />
        <span className="muted">
          {preview.headers.length} columns · {preview.rows.length} rows shown{preview.truncated ? " (more in file)" : ""} ·{" "}
          {preview.encoding ?? preview.sheet ?? ""}
        </span>
        {!disabled ? (
          <button type="button" className="button button--ghost" onClick={onPin} disabled={pinned}>
            {pinned ? "Header pinned" : "Pin this header"}
          </button>
        ) : null}
      </div>
      {preview.issues.length > 0 ? (
        <ul className="issues">
          {preview.issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      ) : null}
      <div className="table-wrap">
        <table className="table table--compact">
          <thead>
            <tr>
              <th>Row</th>
              <th>Status</th>
              {targets.map((t) => (
                <th key={t}>{t}</th>
              ))}
              <th>Issues</th>
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row) => (
              <tr key={row.row_number}>
                <td>{row.row_number}</td>
                <td>
                  <StatusBadge value={row.status} />
                </td>
                {targets.map((t) => (
                  <td key={t} className="mono">
                    {row.raw[t] ?? ""}
                  </td>
                ))}
                <td>{row.issues.map((i) => `${i.code}: ${i.message}`).join("; ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
