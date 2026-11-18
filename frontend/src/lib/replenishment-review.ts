"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Page } from "./api";
import type { FinancialSelection, EvaluationSnapshot } from "./recommendations";
import type { PolicyIdentity } from "./purchasing-policies";

export interface ComparisonChoice { policy_id: string; expected_approved_revision_id: string }
export interface ComparisonRequest { product_id: string; choices: ComparisonChoice[]; financial?: FinancialSelection; order_by: "VENDOR_NAME" | "BASE_PRICE" }
export interface ComparedOffer {
  identity: PolicyIdentity; policy_id: string; approved_revision_id: string; price_comparable: boolean;
  base_price: { reported_price: string; base_units_per_price_unit: number; includes_tax: boolean; exact_numerator: string; exact_denominator: string; display_per_base_unit: string; display_is_rounded: boolean } | null;
  comparison_reasons: string[]; evidence_sha256: string; snapshot: EvaluationSnapshot;
}
export interface VendorComparison {
  product_id: string; catalog_item_number: string; product_name: string; evaluated_at: string;
  selected_count: number; comparable_price_count: number; price_comparison_status: string;
  requested_order: "VENDOR_NAME" | "BASE_PRICE"; applied_order: "VENDOR_NAME" | "BASE_PRICE";
  issues: string[]; offers: ComparedOffer[]; saved: false; stock_reserved: false; purchasing_eligible: false;
}
export interface SavedReviewItem {
  id: string; product_id: string; policy_id: string; approved_revision_id: string; identity: PolicyIdentity; status: string;
  approval_current: boolean; recorded_at: string; evaluated_at: string; demand_window_end: string;
  positive_shortfall: boolean | null; stockout_risk_at_evaluation: boolean | null; supplier_inputs: string;
  commercial_status: string | null; financial_import_id: string | null; reasons: string[];
  reason_count: number; reasons_truncated: boolean; reason: string; historical: true;
}
export interface ReviewSummary {
  generated_at: string; latest_saved_total: number; current_approval_total: number; approval_changed_total: number;
  current_approval_blocked: number; current_approval_review_required: number; current_approval_no_reorder: number;
  current_approval_positive_shortfall: number; historical: true;
}
export interface ReviewFilters { page: number; q: string; product_id: string; vendor_id: string; policy_id: string; status: string; approval: string; shortfall_only: boolean }
export function useReviewPage(filters: ReviewFilters) {
  return useQuery({ queryKey: ["replenishment-review", "page", filters], queryFn: () => api.get<Page<SavedReviewItem>>("/replenishment-review", { ...filters, page_size: 25 }) });
}
export function useReviewSummary() {
  return useQuery({ queryKey: ["replenishment-review", "summary"], queryFn: () => api.get<ReviewSummary>("/replenishment-review/summary") });
}
export function sourceReceipt(offer: ComparedOffer): string | null {
  const evidence = offer.snapshot.supplier?.evidence;
  const file = evidence && typeof evidence === "object" && "file" in evidence ? evidence.file : null;
  return file && typeof file === "object" && "uploaded_at" in file && typeof file.uploaded_at === "string" ? file.uploaded_at : null;
}
