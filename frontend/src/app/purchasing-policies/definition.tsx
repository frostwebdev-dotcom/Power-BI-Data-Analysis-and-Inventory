"use client";

import { JsonBlock, KeyValues, StatusBadge, fmtDate } from "@/components/ui";
import type { PolicyDefinition } from "@/lib/purchasing-policies";
import type { MarketplaceListing } from "@/lib/types";

export function DefinitionView({ definition, listings = [] }: { definition: PolicyDefinition; listings?: Pick<MarketplaceListing, "id" | "seller_sku">[] }) {
  const q = definition.quantity, a = definition.amazon;
  const label = (id: string) => listings.find(l => l.id === id)?.seller_sku ?? "Listing " + id;
  return <div className="policy-definition" data-testid="policy-definition">
    <h3>Demand and coverage</h3><KeyValues rows={[["Marketplace", "Amazon US · Amazon.com"], ["Demand basis", "Fully shipped orders only"], ["Demand window", q.velocity_window_days + " days"], ["Target coverage", q.target_days + " days"], ["Lead time", q.lead_time_days + " days"], ["Safety", q.safety_days + " days"], ["Coverage interpretation", "Target + lead time + safety"], ["Coverage confirmation", definition.coverage_confirmation_reference ?? "Unconfirmed"]]} />
    <h3>Supplier ordering and freshness</h3><KeyValues rows={[["Minimum order", q.minimum_order_units + " base units"], ["Order multiple", q.order_multiple_units + " base units"], ["Base units per supplier order unit", definition.vendor_base_units_per_order_unit ?? "Unknown"], ["Supplier unit evidence", definition.vendor_unit_confirmation_reference ?? "Unconfirmed"], ["Maximum sales-data lag", a.orders_max_age_seconds + " seconds"], ["Maximum inventory age", a.inventory_max_age_seconds + " seconds"], ["Inbound handling", "Only arrivals confirmed before stockout"]]} />
    <h3>Listing conversions</h3>{a.listing_units.length ? <div className="table-wrap" role="region" aria-label="Listing conversions table" tabIndex={0}><table className="table"><thead><tr><th>Seller SKU</th><th>Base units per listing unit</th><th>Evidence</th></tr></thead><tbody>{a.listing_units.map(u => <tr key={u.listing_id}><td className="mono">{label(u.listing_id)}</td><td>{u.base_units_per_listing_unit ?? "Unknown"}</td><td>{u.confirmation_reference ?? "Unconfirmed"}</td></tr>)}</tbody></table></div> : <p className="muted">No listing conversions recorded.</p>}
    <h3>Stock pools</h3>{a.stock_pools.map(pool => <div className="policy-subcard" key={pool.pool_id}><h4>{pool.pool_id} · {pool.channel}</h4><KeyValues rows={[["Member listings", pool.listing_ids.map(label).join(", ")], ["Authoritative stock listing", label(pool.stock_source_listing_id)], ["Distinct stock evidence", pool.distinct_stock_reference ?? "Unconfirmed"]]} /></div>)}
    {!a.stock_pools.length ? <p className="muted">No stock pools recorded.</p> : null}
  </div>;
}

function text(value: unknown): string { return value === null || value === undefined || value === "" ? "Unknown" : typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? String(value) : "Unavailable"; }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
export function MappingEvidence({ context }: { context: Record<string, unknown> }) {
  const product = record(context.product), vendor = record(context.vendor), sku = record(context.vendor_product);
  const listings = Array.isArray(context.listings) ? context.listings.map(record) : [];
  return <details className="policy-evidence"><summary>Identity and mapping evidence checked for this review</summary>
    <KeyValues rows={[["Product", text(product.catalog_item_number) + " · " + text(product.name)], ["Product active", text(product.is_active)], ["Supplier SKU", text(sku.vendor_sku)], ["Vendor active", text(vendor.is_active)], ["Supplier mapping", text(sku.mapping_status)], ["Supplier approval", fmtDate(typeof sku.mapping_approved_at === "string" ? sku.mapping_approved_at : null)]]} />
    <div className="table-wrap" role="region" aria-label="Mapping evidence listings" tabIndex={0}><table className="table"><thead><tr><th>Seller SKU</th><th>ASIN</th><th>Listing</th><th>Mapping</th><th>Approved</th></tr></thead><tbody>{listings.map((l, i) => <tr key={text(l.id) + i}><td>{text(l.seller_sku)}</td><td>{text(l.asin)}</td><td><StatusBadge value={typeof l.listing_status === "string" ? l.listing_status : "UNKNOWN"} /></td><td>{text(l.mapping_status)}</td><td>{fmtDate(typeof l.approved_at === "string" ? l.approved_at : null)}</td></tr>)}</tbody></table></div>
    <details><summary>Recorded review references</summary><JsonBlock value={context} /></details>
  </details>;
}


export function reviewListings(context: Record<string, unknown>) {
  if (!Array.isArray(context.listings)) return [];
  return context.listings.map(record).flatMap(row => typeof row.id === "string" && typeof row.seller_sku === "string" ? [{ id: row.id, seller_sku: row.seller_sku }] : []);
}
