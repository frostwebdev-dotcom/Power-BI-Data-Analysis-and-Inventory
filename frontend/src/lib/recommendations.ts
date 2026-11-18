"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Page } from "./api";
import type { FinancialControl, FinancialProfile } from "./financial-imports";
import type { CommercialPolicy, PolicyRevision } from "./purchasing-policies";

export interface FinancialSelection { import_id: string; expected_binding_revision_id: string | null }
export interface EvaluationRequest { expected_approved_revision_id: string; financial?: FinancialSelection }
export interface RecordRequest extends EvaluationRequest { request_id: string; expected_evidence_sha256: string; reason: string; acknowledge_review: true }
export interface HistoricalFinancialRow {
  listing_id: string; seller_sku: string; source_row_id: string | null; source_row_number: number | null;
  mapping_snapshot: Record<string, unknown> | null; current_mapping_snapshot: Record<string, unknown> | null;
  included_in_totals: boolean; amounts: Record<string, string | null>; computed_profit: string | null;
  computed_margin_percent: string | null; computed_roi_percent: string | null; review_reasons: string[];
}
export interface HistoricalFinancialContext {
  basis: "HISTORICAL_REPORT_ONLY"; import_id: string; binding_revision_id: string | null;
  original_filename: string; raw_sha256: string; profile_sha256: string; profile_snapshot: FinancialProfile;
  marketplace_id: string; store_name: string; currency: "USD"; period_start: string; period_end: string;
  received_at: string; report_status: string; status: string; controls: FinancialControl[]; unallocated_rows: number;
  rows: HistoricalFinancialRow[]; totals: Record<string, string> | null; review_reasons: string[];
}
export interface PoolEvaluation {
  pool_id: string; blocking_reasons: string[];
  inputs: { sales_units: number | null; on_hand_units: number | null; inbound_units: number | null; vendor_stock_units: number | null; vendor_availability: string };
  result: { status: string; reasons: string[]; suggested_units: number | null; calculation: { daily_sales: string | null; days_of_supply: string | null; coverage_days: number; required_units: number | null; net_shortfall_units: number | null; order_quantity_before_vendor_cap: number | null } };
}
export interface SharedStockAllocation {
  status: "COMPLETE" | "PARTIAL" | "BLOCKED" | "UNCONFIRMED";
  supplier_stock_units: number | null; total_allocated_units: number | null; remaining_supplier_units: number | null;
  rows: { pool_id: string; priority: number; requested_units: number | null; allocated_units: number | null; remaining_supplier_units: number | null; reasons: string[] }[];
  reasons: string[]; stock_reserved: false;
}
export interface CommercialAssessment {
  policy: CommercialPolicy;
  price: { status: "VERIFIED" | "UNAVAILABLE"; reported_price: string | null; currency: string | null; base_units_per_price_unit: number | null; issues: string[]; evidence: Record<string, unknown> };
  goods_line_value: string | null; currency: "USD"; supplier_order_units: number | null; price_units: number | null;
  checks: { name: string; scope: string | null; minimum: string | null; observed: string | null; status: string }[];
  status: "ASSESSED" | "INCOMPLETE" | "CONSTRAINT_NOT_MET"; review_reasons: string[]; landed_cost_verified: false;
}
export interface EvaluationSnapshot {
  rule_version: "replenishment-evaluation-v1" | "replenishment-evaluation-v2" | "replenishment-evaluation-v3" | "replenishment-evaluation-v4";
  policy_id: string; product_id: string; vendor_product_id: string; evaluated_at: string;
  approved_policy: PolicyRevision; status: string; policy_blocking_reasons: string[];
  readiness: { status: string; demand_window_start: string; demand_window_end: string; issues: { code: string }[]; [key: string]: unknown };
  supplier_inputs: string; supplier?: Record<string, unknown> | null; pools: PoolEvaluation[];
  allocation?: SharedStockAllocation; commercial?: CommercialAssessment;
  financial?: HistoricalFinancialContext | null; review_reasons?: string[]; profitability: string; purchasing_eligible: false;
}
export interface EvaluationPreview { snapshot: EvaluationSnapshot; evidence_sha256: string }
export interface SavedEvaluation extends EvaluationPreview {
  id: string; request_id: string; policy_id: string; product_id: string; status: string; snapshot_sha256: string;
  recorded_by_user_id: string; recorded_at: string; reason: string; historical: true; purchasing_eligible: false;
}
export const recommendationPath = (id: string) => "/replenishment-recommendations/" + encodeURIComponent(id);
export function useRecommendations(page: number, product: string, policy: string, status: string) {
  return useQuery({ queryKey: ["recommendations", { page, product, policy, status }], queryFn: () => api.get<Page<SavedEvaluation>>("/replenishment-recommendations", { page, page_size: 25, product_id: product, policy_id: policy, status }) });
}
export function useRecommendation(id: string) {
  return useQuery({ queryKey: ["recommendations", id], queryFn: () => api.get<SavedEvaluation>(recommendationPath(id)) });
}
/** JSON numbers outside this range have already lost precision: never present them as exact. */
export function exactDisplay(value: unknown): unknown {
  if (typeof value === "number" && !Number.isSafeInteger(value)) return "Outside exact display range";
  if (Array.isArray(value)) return value.map(exactDisplay);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, exactDisplay(v)]));
  return value;
}
export function hasUnsafeQuantity(value: unknown): boolean {
  if (typeof value === "number") return !Number.isSafeInteger(value);
  if (Array.isArray(value)) return value.some(hasUnsafeQuantity);
  return Boolean(value && typeof value === "object" && Object.values(value).some(hasUnsafeQuantity));
}
export function quantity(value: number | null | undefined) { return value == null ? "Unknown" : Number.isSafeInteger(value) ? value.toLocaleString("en-US") : "Outside exact display range"; }
