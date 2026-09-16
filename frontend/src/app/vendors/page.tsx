"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import type { FormEvent } from "react";

import { Drawer, EmptyState, ErrorNote, Loading, PageHeader, Pager, StatusBadge, fmtDate } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useVendors } from "@/lib/queries";
import type { Vendor } from "@/lib/types";

interface VendorForm {
  code: string;
  name: string;
  currency: string;
  contact_email: string;
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
  default_lead_time_days: "",
  minimum_order_quantity: "",
  minimum_order_value: "",
  notes: "",
};

function toPayload(form: VendorForm, includeCode: boolean): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    currency: form.currency.trim() || "USD",
    contact_email: form.contact_email.trim() || null,
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
    default_lead_time_days: "",
    minimum_order_quantity: vendor.minimum_order_quantity?.toString() ?? "",
    minimum_order_value: vendor.minimum_order_value ?? "",
    notes: "",
  };
}

export default function VendorsPage() {
  const { hasRole } = useAuth();
  const canWrite = hasRole("DATA_OPERATOR");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<Vendor | "new" | null>(null);

  const vendors = useVendors({ page, page_size: 25, q: search, status });

  return (
    <>
      <PageHeader
        title="Vendors"
        lede="Suppliers, their purchasing terms, and the import profiles that read their files."
        actions={
          canWrite ? (
            <button type="button" className="button button--primary" onClick={() => setEditing("new")}>
              New vendor
            </button>
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
        ? api.patch<Vendor>(`/vendors/${vendor.id}`, toPayload(form, false))
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
