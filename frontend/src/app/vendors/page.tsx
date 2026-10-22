"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import type { FormEvent } from "react";

import { Drawer, EmptyState, ErrorNote, Loading, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { VendorBulkSetup } from "@/components/VendorBulkSetup";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useVendors } from "@/lib/queries";
import type { Vendor } from "@/lib/types";

interface VendorForm {
  code: string;
  name: string;
  currency: string;
  contact_email: string;
  contact_phone: string;
  timezone: string;
  payment_terms: string;
  freight_terms: string;
  inventory_cadence: string;
  default_lead_time_days: string;
  minimum_order_quantity: string;
  minimum_order_value: string;
  notes: string;
}

const EMPTY: VendorForm = {
  code: "",
  name: "",
  currency: "USD",
  contact_email: "",
  contact_phone: "",
  timezone: "UTC",
  payment_terms: "",
  freight_terms: "",
  inventory_cadence: "",
  default_lead_time_days: "",
  minimum_order_quantity: "",
  minimum_order_value: "",
  notes: "",
};

function toPayload(form: VendorForm, includeCode: boolean, savedTerms: Record<string, unknown> = {}): Record<string, unknown> {
  const terms = { ...savedTerms };
  for (const [key, value] of [["payment", form.payment_terms], ["freight", form.freight_terms], ["inventory_cadence", form.inventory_cadence]] as const) {
    if (value.trim()) terms[key] = value.trim();
    else delete terms[key];
  }
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    currency: form.currency.trim() || "USD",
    contact_email: form.contact_email.trim() || null,
    contact_phone: form.contact_phone.trim() || null,
    timezone: form.timezone.trim() || "UTC",
    purchasing_terms: terms,
    default_lead_time_days: form.default_lead_time_days ? Number(form.default_lead_time_days) : null,
    minimum_order_quantity: form.minimum_order_quantity ? Number(form.minimum_order_quantity) : null,
    minimum_order_value: form.minimum_order_value ? form.minimum_order_value : null,
    notes: form.notes.trim() || null,
  };
  if (includeCode) payload.code = form.code.trim();
  return payload;
}

function fromVendor(vendor: Vendor): VendorForm {
  return {
    code: vendor.code,
    name: vendor.name,
    currency: vendor.currency ?? "USD",
    contact_email: vendor.contact_email ?? "",
    contact_phone: vendor.contact_phone ?? "",
    timezone: vendor.timezone ?? "UTC",
    payment_terms: String(vendor.purchasing_terms?.payment ?? ""),
    freight_terms: String(vendor.purchasing_terms?.freight ?? ""),
    inventory_cadence: String(vendor.purchasing_terms?.inventory_cadence ?? ""),
    default_lead_time_days: vendor.default_lead_time_days?.toString() ?? "",
    minimum_order_quantity: vendor.minimum_order_quantity?.toString() ?? "",
    minimum_order_value: vendor.minimum_order_value ?? "",
    notes: vendor.notes ?? "",
  };
}

export default function VendorsPage() {
  const { hasRole } = useAuth();
  const canWrite = hasRole("DATA_OPERATOR");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<Vendor | "new" | null>(null);
  const [bulkSetup, setBulkSetup] = useState(false);

  const vendors = useVendors({ page, page_size: 25, q: search, status });

  return (
    <>
      <PageHeader
        title="Vendors"
        lede="Suppliers, their purchasing terms, and the import profiles that read their files."
        actions={
          canWrite ? (
            <>
              <button type="button" className="button" onClick={() => setBulkSetup(true)}>Set up vendor list</button>
              <button type="button" className="button button--primary" onClick={() => setEditing("new")}>
                New vendor
              </button>
            </>
          ) : null
        }
      />
      <div className="toolbar">
        <input
          type="search"
          placeholder="Search by name or code"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(1);
          }}
        />
        <select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            setPage(1);
          }}
          aria-label="Status"
        >
          <option value="">Any status</option>
          <option value="ACTIVE">Active</option>
          <option value="ON_HOLD">On hold</option>
          <option value="INACTIVE">Inactive</option>
        </select>
      </div>
      <ErrorNote error={vendors.error} />
      {vendors.data ? (
        <p className="muted small" role="status">{vendors.data.total} vendors{search || status ? " matching the filters" : " configured"}</p>
      ) : null}
      {vendors.isPending ? <Loading what="Loading vendors" /> : null}
      {vendors.data && vendors.data.items.length === 0 ? (
        <EmptyState title="No vendors yet" text={canWrite ? "Create the first one with “New vendor”." : undefined} />
      ) : null}
      {vendors.data && vendors.data.items.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Code</th>
                <th>Name</th>
                <th>Status</th>
                <th>Currency</th>
                <th>Contact</th>
                <th>MOQ</th>
                <th>Updated</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {vendors.data.items.map((vendor) => (
                <tr key={vendor.id} data-testid={`vendor-row-${vendor.code}`}>
                  <td className="mono">{vendor.code}</td>
                  <td>{vendor.name}</td>
                  <td>
                    <StatusBadge value={vendor.is_active ? vendor.status : "INACTIVE"} />
                  </td>
                  <td>{vendor.currency ?? "—"}</td>
                  <td>{vendor.contact_email ?? "—"}</td>
                  <td>{vendor.minimum_order_quantity ?? "—"}</td>
                  <td>{fmtDate(vendor.updated_at)}</td>
                  <td className="actions">
                    <Link href={`/profiles?vendor=${vendor.id}`} className="button button--ghost">
                      Profiles
                    </Link>
                    {canWrite ? (
                      <button type="button" className="button button--ghost" onClick={() => setEditing(vendor)}>
                        Edit
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager page={vendors.data.page} pageSize={vendors.data.page_size} total={vendors.data.total} onPage={setPage} />
        </div>
      ) : null}
      {editing ? <VendorEditor vendor={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
      {bulkSetup && canWrite ? <VendorBulkSetup onClose={() => setBulkSetup(false)} /> : null}
    </>
  );
}

function VendorEditor({ vendor, onClose }: { vendor: Vendor | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<VendorForm>(vendor ? fromVendor(vendor) : EMPTY);
  const set = (key: keyof VendorForm) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  const save = useMutation({
    mutationFn: () =>
      vendor
        ? api.patch<Vendor>(`/vendors/${vendor.id}`, toPayload(form, false, vendor.purchasing_terms))
        : api.post<Vendor>("/vendors", toPayload(form, true)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["vendors"] });
      onClose();
    },
  });
  const deactivate = useMutation({
    mutationFn: () => api.post<Vendor>(`/vendors/${vendor?.id}/deactivate`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["vendors"] });
      onClose();
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate();
  }

  return (
    <Drawer title={vendor ? `Edit ${vendor.code}` : "New vendor"} onClose={onClose}>
      <form onSubmit={submit} className="form" data-testid="vendor-form">
        {!vendor ? (
          <label className="field">
            <span className="field__label">Code</span>
            <input name="code" required value={form.code} onChange={set("code")} placeholder="ACME" />
            <span className="field__hint">Short identifier, upper-cased. Cannot change later.</span>
          </label>
        ) : null}
        <label className="field">
          <span className="field__label">Name</span>
          <input name="name" required value={form.name} onChange={set("name")} />
        </label>
        <div className="grid grid--2">
          <label className="field">
            <span className="field__label">Currency</span>
            <input name="currency" value={form.currency} onChange={set("currency")} maxLength={3} />
          </label>
          <label className="field">
            <span className="field__label">Contact email</span>
            <input name="contact_email" type="email" value={form.contact_email} onChange={set("contact_email")} />
          </label>
          <label className="field">
            <span className="field__label">Default lead time (days)</span>
            <input name="default_lead_time_days" type="number" min={0} value={form.default_lead_time_days} onChange={set("default_lead_time_days")} />
          </label>
          <label className="field">
            <span className="field__label">Minimum order quantity</span>
            <input name="minimum_order_quantity" type="number" min={0} value={form.minimum_order_quantity} onChange={set("minimum_order_quantity")} />
          </label>
          <label className="field">
            <span className="field__label">Minimum order value</span>
            <input name="minimum_order_value" inputMode="decimal" value={form.minimum_order_value} onChange={set("minimum_order_value")} />
          </label>
        </div>
        <div className="grid grid--2">
          <label className="field"><span className="field__label">Contact phone</span><input name="contact_phone" value={form.contact_phone} onChange={set("contact_phone")} /></label>
          <label className="field"><span className="field__label">Vendor timezone</span><input name="timezone" value={form.timezone} onChange={set("timezone")} /></label>
          <label className="field"><span className="field__label">Payment terms</span><input name="payment_terms" value={form.payment_terms} onChange={set("payment_terms")} /></label>
          <label className="field"><span className="field__label">Freight terms</span><input name="freight_terms" value={form.freight_terms} onChange={set("freight_terms")} /></label>
          <label className="field"><span className="field__label">Inventory cadence</span><input name="inventory_cadence" value={form.inventory_cadence} onChange={set("inventory_cadence")} /></label>
        </div>
        <label className="field">
          <span className="field__label">Notes</span>
          <textarea name="notes" rows={3} value={form.notes} onChange={set("notes")} />
        </label>
        <ErrorNote error={save.error ?? deactivate.error} />
        <div className="form__actions">
          <button type="submit" className="button button--primary" disabled={save.isPending}>
            {vendor ? "Save changes" : "Create vendor"}
          </button>
          {vendor?.is_active ? (
            <button
              type="button"
              className="button button--danger"
              disabled={deactivate.isPending}
              onClick={() => {
                if (window.confirm(`Deactivate ${vendor.code}? Its history is kept.`)) deactivate.mutate();
              }}
            >
              Deactivate
            </button>
          ) : null}
        </div>
      </form>
    </Drawer>
  );
}
