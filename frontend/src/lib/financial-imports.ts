import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "./api";
import type { Page } from "./api";

export type FinancialMappingStatus = "LINKED" | "UNRESOLVED" | "UNALLOCATED" | "INVALID_SOURCE" | "ASIN_CONFLICT";
export interface FinancialProfile {
  layout_version: "connectbooks-signed-pnl-xlsx-v1";
  sheet_name: string;
  store_name: string;
  source_store_name: string;
  marketplace_id: string;
  currency: "USD";
  reporting_month: number;
  reporting_year: number | null;
  reporting_timezone: string | null;
  advertising_coverage_confirmed: boolean;
}
export interface FinancialIssue { code: string; field: string; severity: "ERROR" | "REVIEW" | "NOTICE" }
export interface FinancialControl {
  field: string;
  detail_total: string | null;
  reported_total: string | null;
  difference: string | null;
  status: "MATCH" | "MISMATCH" | "INCOMPLETE";
}
export interface FinancialImport {
  id: string;
  original_filename: string;
  raw_sha256: string;
  profile_sha256: string;
  marketplace_id: string;
  store_name: string;
  currency: "USD";
  period_start: string;
  period_end: string;
  received_at: string;
  status: "RECORDED" | "REVIEW_REQUIRED";
  row_count: number;
  linked_rows: number;
  review_rows: number;
  purchasing_eligible: false;
  profile_snapshot: FinancialProfile;
  report_snapshot: {
    controls?: FinancialControl[];
    issues?: FinancialIssue[];
    unallocated_rows?: number;
  };
}
export interface FinancialRow {
  id: string;
  import_id: string;
  source_row_number: number;
  seller_sku: string | null;
  row_kind: "SKU" | "UNALLOCATED";
  mapping_status: FinancialMappingStatus;
  listing_id: string | null;
  product_id: string | null;
  match_priority: 4 | null;
  mapping_snapshot: {
    approved_by_user_id?: string;
    approved_at?: string;
    mapping_method?: string;
  };
  source_snapshot: {
    source_item: string | null;
    asin: string | null;
    amounts: Record<string, string | null>;
    issues: FinancialIssue[];
    reported_margin_percent: string | null;
    reported_roi_percent: string | null;
    computed_margin_percent: string | null;
    computed_roi_percent: string | null;
  };
  review_reasons: string[];
  review_required: boolean;
  purchasing_eligible: false;
  net_sales: string | null;
  fees: string | null;
  cost: string | null;
  ad_spend: string | null;
  reported_profit: string | null;
  computed_profit: string | null;
}
export interface FinancialUpload { batch: FinancialImport; duplicate: boolean }

export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
export const MAX_FINANCIAL_BYTES = 8 * 1024 * 1024;

/** Display decimal strings exactly: no binary floating-point conversion or arithmetic. */
export function financialNumber(value: string | null | undefined, money = true): string {
  if (value === null || value === undefined || value === "") return "Unknown";
  const match = /^(-?)([0-9]+)(?:\.([0-9]+))?(?:[Ee]([+-]?[0-9]{1,2}))?$/.exec(value);
  if (!match) return "Unavailable";
  const whole = match[2] ?? "";
  const digits = whole + (match[3] ?? "");
  // The exponent moves a string position; the financial value is never a Number.
  const point = whole.length + Number(match[4] ?? "0");
  const expanded = point <= 0 ? "0." + "0".repeat(-point) + digits
    : point >= digits.length ? digits + "0".repeat(point - digits.length)
    : digits.slice(0, point) + "." + digits.slice(point);
  const parts = expanded.split(".");
  const integer = (parts[0] ?? "").replace(/^0+(?=[0-9])/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fraction = (parts[1] ?? "").replace(/0+$/, "").padEnd(money ? 2 : 0, "0");
  return (match[1] ?? "") + integer + (fraction ? "." + fraction : "");
}

export function financialPeriod(profile: FinancialProfile): string {
  const month = MONTHS[profile.reporting_month - 1] ?? "Month unconfirmed";
  return month + " " + (profile.reporting_year ?? "year unconfirmed");
}

const REASONS: Record<string, string> = {
  ZERO_COST_WITH_POSITIVE_SALES: "Positive net sales with zero reported cost. Confirm the product cost.",
  UNALLOCATED_FINANCIAL_ROW: "Unitemized amounts are included in totals without a product link.",
  MAPPING_UNALLOCATED: "Unallocated financial amount; no seller SKU or product is assigned.",
  MAPPING_UNRESOLVED: "No active approved Amazon SKU mapping was available when this report was imported.",
  MAPPING_ASIN_CONFLICT: "The report ASIN conflicts with the approved listing. Review the source and mapping.",
  MAPPING_INVALID_SOURCE: "Invalid source values prevented a product link.",
  ADVERTISING_COVERAGE_UNCONFIRMED: "Advertising cost coverage has not been confirmed for this report.",
  REPORTING_YEAR_UNCONFIRMED: "The report year has not been confirmed.",
  REPORTING_TIMEZONE_UNCONFIRMED: "The report time zone has not been confirmed.",
  MISSING_AMOUNT: "An amount is missing. It remains unknown.",
  INVALID_AMOUNT: "An amount could not be read as a valid source value.",
  MISSING_ASIN: "The report has no ASIN for this row.",
  DUPLICATE_SELLER_SKU: "The seller SKU appears more than once. Review the source rows.",
  PROFIT_RECONCILIATION_MISMATCH: "Reported profit does not reconcile with the source amounts.",
  NET_SALES_RECONCILIATION_MISMATCH: "Net sales do not reconcile with sales, refunds and adjustments.",
  CONTROL_TOTAL_MISMATCH: "The source detail does not match the report's final total.",
  CONTROL_TOTAL_INCOMPLETE: "Missing or invalid values prevent this total from being checked.",
};
export function financialReason(code: string): string {
  return REASONS[code] ?? code.replaceAll("_", " ").toLowerCase();
}
export function financialError(error: unknown): unknown {
  if (!(error instanceof ApiError)) return error;
  const detail = error.details.code;
  if (detail === "OVERLAPPING_REPORTING_PERIOD") return new Error("A report already covers this period. Open the existing import to review it. Replacement reports are not available yet.");
  if (detail === "REPLAY_CONTEXT_MISMATCH") return new Error("This file was already recorded with different reporting details. Open the original import; its reporting context cannot be changed by re-uploading.");
  if (detail === "RAW_INTEGRITY_MISMATCH") return new Error("The original file could not be verified. Ask an administrator to check the retained file.");
  return error;
}

export function useFinancialImports(page: number) {
  return useQuery({
    queryKey: ["financial-imports", "history", page],
    queryFn: () => api.get<Page<FinancialImport>>("/financial-imports", { page, page_size: 25 }),
  });
}
export function useFinancialImport(id: string) {
  return useQuery({ queryKey: ["financial-imports", id], queryFn: () => api.get<FinancialImport>("/financial-imports/" + encodeURIComponent(id)) });
}
export function useFinancialRows(id: string, page: number, reviewOnly: boolean, mapping: string) {
  return useQuery({
    queryKey: ["financial-imports", id, "rows", page, reviewOnly, mapping],
    queryFn: () => api.get<Page<FinancialRow>>("/financial-imports/" + encodeURIComponent(id) + "/rows", {
      page, page_size: 25, review_only: reviewOnly, mapping_status: mapping,
    }),
  });
}
