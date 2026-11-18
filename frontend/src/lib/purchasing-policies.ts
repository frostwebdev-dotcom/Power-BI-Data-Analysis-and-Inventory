"use client";

import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "./api";
import type { Page } from "./api";
import type { ImportProfile, MarketplaceListing } from "./types";

export const US_MARKETPLACE = "ATVPDKIKX0DER";
export interface ListingUnits { listing_id: string; base_units_per_listing_unit: number | null; confirmation_reference: string | null }
export interface StockPool { pool_id: string; channel: "FBA" | "FBM"; listing_ids: string[]; stock_source_listing_id: string; distinct_stock_reference: string | null }
export interface SupplierPolicy { profile_id: string | null; inventory_max_age_seconds: number; freshness_basis: "FILE_RECEIPT_TIME"; freshness_confirmation_reference: string | null; base_units_per_source_quantity: number | null; quantity_confirmation_reference: string | null; availability_confirmation_reference: string | null }
export interface PolicyDefinition {
  supplier?: SupplierPolicy;
  rule_version: "purchasing-policy-v1";
  quantity: { velocity_window_days: 7 | 14 | 30; target_days: number; lead_time_days: number; safety_days: number; minimum_order_units: number; order_multiple_units: number };
  amazon: { product_id: string; marketplace_id: typeof US_MARKETPLACE; sales_channel: "Amazon.com"; demand_basis: "FULLY_SHIPPED_ONLY"; velocity_window_days: 7 | 14 | 30; orders_max_age_seconds: number; inventory_max_age_seconds: number; listing_units: ListingUnits[]; stock_pools: StockPool[] };
  coverage_policy: "target-plus-lead-plus-safety";
  coverage_confirmation_reference: string | null;
  inbound_policy: "CONFIRMED_BEFORE_STOCKOUT_ONLY";
  vendor_base_units_per_order_unit: number | null;
  vendor_unit_confirmation_reference: string | null;
}
export interface PolicyRevision {
  id: string; policy_id: string; product_id: string; vendor_product_id: string; marketplace_id: string;
  revision_number: number; supersedes_revision_id: string | null; reviewed_revision_id: string | null;
  kind: "DRAFT" | "APPROVED" | "REVOKED"; definition: PolicyDefinition; definition_sha256: string;
  review_context: Record<string, unknown> | null; context_sha256: string | null;
  recorded_by_user_id: string; recorded_at: string; reason: string; purchasing_eligible: false;
}
export interface PolicyIdentity { catalog_item_number: string; product_name: string; vendor_id: string; vendor_name: string; vendor_code: string; vendor_sku: string }
export interface PolicySummary { identity: PolicyIdentity; policy_id: string; head: PolicyRevision; approved: PolicyRevision | null; current_data_verified: false; purchasing_eligible: false }
export type ReviewAction = "APPROVE" | "REVOKE";
export interface PolicyReview {
  action: ReviewAction; policy_id: string; expected_head_id: string; reviewed_revision_id: string;
  definition: PolicyDefinition; definition_sha256: string; review_context: Record<string, unknown>;
  context_sha256: string; blocking_reasons: string[]; review_allowed: boolean; purchasing_eligible: false;
}
export interface VendorProductChoice { id: string; product_id: string; vendor_id: string; vendor_name: string; vendor_code: string; vendor_sku: string; is_active: boolean; vendor_is_active: boolean; mapping_status: string; mapping_method: string | null; approved_by_user_id: string | null; approved_at: string | null }
export const policyPath = (id: string) => "/purchasing-policies/" + encodeURIComponent(id);
export function usePolicies(page: number, productId = "") {
  return useQuery({ queryKey: ["purchasing-policies", { page, productId }], queryFn: () => api.get<Page<PolicySummary>>("/purchasing-policies", { page, page_size: 25, product_id: productId }) });
}
export function usePolicy(id: string) {
  return useQuery({ queryKey: ["purchasing-policies", id], queryFn: () => api.get<PolicySummary>(policyPath(id)) });
}
export function usePolicyHistory(id: string, page: number) {
  return useQuery({ queryKey: ["purchasing-policies", id, "history", page], queryFn: () => api.get<Page<PolicyRevision>>(policyPath(id) + "/revisions", { page, page_size: 10 }) });
}
export function usePolicyListings(productId: string, marketplace: string) {
  return useQuery({ queryKey: ["policy-listings", productId, marketplace], enabled: Boolean(productId && marketplace), queryFn: async () => {
    const data = await api.get<Page<MarketplaceListing>>("/products/" + encodeURIComponent(productId) + "/listings", { marketplace: "AMAZON", marketplace_id: marketplace, page_size: 200 });
    if (data.total > 200 || data.items.length !== data.total) throw new Error("The complete Amazon listing set could not be loaded. Policy editing supports up to 200 listings per product.");
    return data.items;
  } });
}
export function useVendorProductChoices(productId: string, page: number, q: string) {
  return useQuery({ queryKey: ["policy-vendor-products", productId, page, q], enabled: Boolean(productId), queryFn: () => api.get<Page<VendorProductChoice>>("/products/" + encodeURIComponent(productId) + "/vendor-products", { page, page_size: 10, q }) });
}
export function useSupplierProfiles(vendorId: string, enabled: boolean) {
  return useQuery({ queryKey: ["policy-supplier-profiles", vendorId], enabled: enabled && Boolean(vendorId), queryFn: async () => {
    const data = await api.get<{ items: ImportProfile[]; total: number }>("/vendors/" + encodeURIComponent(vendorId) + "/import-profiles", { include_inactive: true });
    if (data.total > 200 || data.items.length !== data.total) throw new Error("The complete supplier profile list could not be loaded. Select from up to 200 profiles.");
    return data.items;
  } });
}

const REASONS: Record<string, string> = {
  SUPPLIER_PROFILE_UNCONFIRMED: "Choose a confirmed inventory profile belonging to this supplier.",
  SUPPLIER_PROFILE_INACTIVE: "The chosen supplier profile is inactive or superseded.",
  SUPPLIER_PROFILE_INVALID: "The chosen supplier profile has invalid extraction rules.",
  SUPPLIER_QUANTITY_UNITS_UNCONFIRMED: "Confirm the source quantity conversion and its evidence.",
  SUPPLIER_RECEIPT_AGE_POLICY_UNCONFIRMED: "Confirm using file receipt time to check supplier freshness.",
  SUPPLIER_AVAILABILITY_POLICY_UNCONFIRMED: "Confirm how this supplier file reports availability.",
  CURRENT_HEAD_NOT_DRAFT: "The latest version is not a draft. Save a new draft before requesting approval.",
  PRODUCT_INACTIVE: "The product is inactive.", VENDOR_INACTIVE: "The vendor is inactive.",
  VENDOR_MAPPING_UNCONFIRMED: "The supplier SKU needs an active, approved product mapping and approval evidence.",
  AMAZON_MAPPING_UNCONFIRMED: "An Amazon listing needs an active, approved mapping, ASIN and approval evidence.",
  LISTING_CONFIGURATION_UNPROVEN: "The complete known Amazon listing set is unavailable.",
  LISTING_CONFIGURATION_INCOMPLETE: "The policy does not include every current Amazon listing.",
  UNIT_CONVERSION_UNCONFIRMED: "Confirm every Amazon listing conversion and its evidence reference.",
  STOCK_POOLS_UNCONFIRMED: "Define the stock pools and confirm which physical stock each represents.",
  STOCK_POOL_MEMBERSHIP_INCOMPLETE: "Assign every known listing to a stock pool and remove obsolete members.",
  COVERAGE_INTERPRETATION_UNCONFIRMED: "Confirm that target days exclude the separately added lead and safety days.",
  VENDOR_UNITS_UNCONFIRMED: "Confirm the supplier order-unit conversion and evidence reference.",
  ORDER_MULTIPLE_CONFLICTS_WITH_VENDOR_UNITS: "The order multiple must contain whole supplier order units.",
};
export function policyReason(code: string) { return REASONS[code] ?? code.replaceAll("_", " ").toLowerCase(); }
export function policyError(error: unknown): Error {
  if (!(error instanceof ApiError)) return error instanceof Error ? error : new Error("The policy request could not be completed.");
  const code = error.details.code;
  if (code === "POLICY_HEAD_CHANGED") return new Error("A newer policy version exists. Your draft is still here. Close the editor and reopen the latest version before applying your changes again.");
  if (code === "POLICY_REVIEW_CHANGED") return new Error("This review is no longer current. Preview again and check the latest definition and mappings.");
  if (code === "POLICY_ALREADY_EXISTS_OR_CHANGED") return new Error("A policy already exists for this product and supplier SKU, or it changed. Open its history before continuing.");
  if (code === "POLICY_NO_CHANGES") return new Error("The definition has not changed. There is no new draft to save.");
  if (code === "POLICY_REVIEW_BLOCKED") return new Error("Approval is blocked. Preview again to review the unresolved assumptions.");
  if (code === "NO_APPROVED_POLICY") return new Error("There is no current approval to revoke. Reload the policy.");
  if (error.statusCode === 403) return new Error("Your current role cannot perform this action. Reload the page to check your access.");
  if (error.statusCode === 404) return new Error("The policy or selected product / supplier SKU is no longer available to your account.");
  if (error.statusCode === 422) return new Error("Some policy fields are invalid. Check the whole-number values, evidence references and stock-pool membership.");
  return new Error(error.message);
}

export interface UnitForm { listing_id: string; factor: string; reference: string }
export interface PoolForm { key: string; name: string; channel: string; members: string[]; source: string; reference: string }
export interface PolicyForm {
  marketplace: string; window: string; target: string; lead: string; safety: string; minimum: string; multiple: string;
  ordersAge: string; inventoryAge: string; demand: string; coverage: string; coverageReference: string;
  supplierEnabled: boolean; supplierProfile: string; supplierAge: string; supplierBasis: string; supplierFreshnessReference: string; supplierFactor: string; supplierQuantityReference: string; supplierAvailabilityReference: string;
  inbound: string; vendorFactor: string; vendorReference: string; units: UnitForm[]; pools: PoolForm[];
}
export function policyForm(definition?: PolicyDefinition): PolicyForm {
  if (!definition) return { supplierEnabled: false, supplierProfile: "", supplierAge: "", supplierBasis: "", supplierFreshnessReference: "", supplierFactor: "", supplierQuantityReference: "", supplierAvailabilityReference: "", marketplace: "", window: "", target: "", lead: "", safety: "", minimum: "", multiple: "", ordersAge: "", inventoryAge: "", demand: "", coverage: "", coverageReference: "", inbound: "", vendorFactor: "", vendorReference: "", units: [], pools: [] };
  const q = definition.quantity, a = definition.amazon;
  const supplier = definition.supplier;
  return { supplierEnabled: Boolean(supplier), supplierProfile: supplier?.profile_id ?? "", supplierAge: supplier ? String(supplier.inventory_max_age_seconds) : "", supplierBasis: supplier?.freshness_basis ?? "", supplierFreshnessReference: supplier?.freshness_confirmation_reference ?? "", supplierFactor: supplier?.base_units_per_source_quantity?.toString() ?? "", supplierQuantityReference: supplier?.quantity_confirmation_reference ?? "", supplierAvailabilityReference: supplier?.availability_confirmation_reference ?? "", marketplace: a.marketplace_id, window: String(q.velocity_window_days), target: String(q.target_days), lead: String(q.lead_time_days), safety: String(q.safety_days), minimum: String(q.minimum_order_units), multiple: String(q.order_multiple_units), ordersAge: String(a.orders_max_age_seconds), inventoryAge: String(a.inventory_max_age_seconds), demand: a.demand_basis, coverage: definition.coverage_policy, coverageReference: definition.coverage_confirmation_reference ?? "", inbound: definition.inbound_policy, vendorFactor: definition.vendor_base_units_per_order_unit?.toString() ?? "", vendorReference: definition.vendor_unit_confirmation_reference ?? "", units: a.listing_units.map(u => ({ listing_id: u.listing_id, factor: u.base_units_per_listing_unit?.toString() ?? "", reference: u.confirmation_reference ?? "" })), pools: a.stock_pools.map((p, i) => ({ key: String(i), name: p.pool_id, channel: p.channel, members: [...p.listing_ids], source: p.stock_source_listing_id, reference: p.distinct_stock_reference ?? "" })) };
}
function integer(value: string, label: string, min: number, max: number): number {
  if (!/^\d+$/.test(value.trim())) throw new Error(label + " must be an explicit whole number.");
  const result = Number(value.trim());
  if (!Number.isSafeInteger(result) || result < min || result > max) throw new Error(label + " must be between " + min + " and " + max + ".");
  return result;
}
function reference(value: string, label: string): string | null {
  const text = value.trim();
  if (!text) return null;
  if (text.length < 10 || text.length > 500) throw new Error(label + " must contain 10–500 characters, or be left blank while unknown.");
  return text;
}
export function buildPolicy(form: PolicyForm, productId: string, units: UnitForm[]): PolicyDefinition {
  if (form.marketplace !== US_MARKETPLACE || form.demand !== "FULLY_SHIPPED_ONLY" || form.coverage !== "target-plus-lead-plus-safety" || form.inbound !== "CONFIRMED_BEFORE_STOCKOUT_ONLY") throw new Error("Choose the marketplace, demand basis, coverage interpretation and inbound handling explicitly.");
  const window = integer(form.window, "Demand window", 7, 30);
  if (window !== 7 && window !== 14 && window !== 30) throw new Error("Choose a 7, 14 or 30 day demand window.");
  const poolNames = new Set<string>(), memberships = new Set<string>();
  const pools: StockPool[] = form.pools.map(pool => {
    const name = pool.name.trim();
    if (!/^[A-Za-z0-9._-]{1,64}$/.test(name) || poolNames.has(name)) throw new Error("Give every stock pool a unique name using letters, numbers, dot, dash or underscore.");
    poolNames.add(name);
    if (pool.channel !== "FBA" && pool.channel !== "FBM") throw new Error("Choose a fulfillment channel for each pool.");
    if (!pool.members.length || !pool.members.includes(pool.source)) throw new Error("Choose pool members and one stock source from those members.");
    for (const id of pool.members) { const key = pool.channel + ":" + id; if (memberships.has(key)) throw new Error("A listing can belong to one stock pool per fulfillment channel."); memberships.add(key); }
    return { pool_id: name, channel: pool.channel, listing_ids: [...pool.members], stock_source_listing_id: pool.source, distinct_stock_reference: reference(pool.reference, "Stock-pool evidence") };
  });
  if (form.supplierEnabled && form.supplierBasis !== "FILE_RECEIPT_TIME") throw new Error("Choose the supplier freshness basis explicitly.");
  return {
    ...(form.supplierEnabled ? { supplier: { profile_id: form.supplierProfile || null, inventory_max_age_seconds: integer(form.supplierAge, "Maximum supplier file receipt age", 1, 2678400), freshness_basis: "FILE_RECEIPT_TIME" as const, freshness_confirmation_reference: reference(form.supplierFreshnessReference, "Supplier receipt-age confirmation"), base_units_per_source_quantity: form.supplierFactor.trim() ? integer(form.supplierFactor, "Source quantity conversion", 1, 1000000) : null, quantity_confirmation_reference: reference(form.supplierQuantityReference, "Source quantity evidence"), availability_confirmation_reference: reference(form.supplierAvailabilityReference, "Supplier availability evidence") } } : {}),
    rule_version: "purchasing-policy-v1",
    quantity: { velocity_window_days: window, target_days: integer(form.target, "Target coverage days", 1, 3650), lead_time_days: integer(form.lead, "Lead time days", 0, 3650), safety_days: integer(form.safety, "Safety days", 0, 3650), minimum_order_units: integer(form.minimum, "Minimum order", 0, 1000000000), order_multiple_units: integer(form.multiple, "Order multiple", 1, 1000000000) },
    amazon: { product_id: productId, marketplace_id: US_MARKETPLACE, sales_channel: "Amazon.com", demand_basis: "FULLY_SHIPPED_ONLY", velocity_window_days: window, orders_max_age_seconds: integer(form.ordersAge, "Maximum sales-data lag", 1, 2678400), inventory_max_age_seconds: integer(form.inventoryAge, "Maximum inventory age", 1, 2678400), listing_units: units.map(u => ({ listing_id: u.listing_id, base_units_per_listing_unit: u.factor.trim() ? integer(u.factor, "Listing conversion", 1, 1000000) : null, confirmation_reference: reference(u.reference, "Listing conversion evidence") })), stock_pools: pools },
    coverage_policy: "target-plus-lead-plus-safety", coverage_confirmation_reference: reference(form.coverageReference, "Coverage confirmation"), inbound_policy: "CONFIRMED_BEFORE_STOCKOUT_ONLY", vendor_base_units_per_order_unit: form.vendorFactor.trim() ? integer(form.vendorFactor, "Supplier order-unit conversion", 1, 1000000) : null, vendor_unit_confirmation_reference: reference(form.vendorReference, "Supplier unit evidence"),
  };
}
