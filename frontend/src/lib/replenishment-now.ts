"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Page } from "./api";
import type { PolicyIdentity } from "./purchasing-policies";
import type { InboundSelection } from "./inbound";
import type { ComparisonChoice } from "./replenishment-review";
import type { EvaluationSnapshot } from "./recommendations";

export type Urgency = "URGENT" | "REPLENISH" | "NO_REORDER" | "UNKNOWN";
export type Opportunity = "REVIEW_OPPORTUNITY" | "UNAVAILABLE" | "UNCONFIRMED" | "NO_SHORTFALL";
export interface CurrentPolicyChoice { policy_id: string; product_id: string; approved_revision_id: string; approved_revision_number: number; identity: PolicyIdentity; inbound?: InboundSelection }
export interface CurrentPoolSignal {
  pool_id: string; channel: string; urgency: Urgency; sales_base_units: number | null;
  on_hand_base_units: number | null; usable_inbound_base_units: number | null;
  window_days: number; lead_plus_safety_days: number; required_units: number | null; net_shortfall_units: number | null;
  supply_days_numerator: string | null; supply_days_denominator: string | null; reasons: string[];
}
export interface CurrentPolicyCheck {
  identity: PolicyIdentity; policy_id: string; product_id: string; approved_revision_id: string;
  urgency: Urgency; opportunity: Opportunity; reasons: string[]; pools: CurrentPoolSignal[];
  snapshot: EvaluationSnapshot; evidence_sha256: string; purchasing_eligible: false;
}
export interface CurrentCheck {
  evaluated_at: string; scope: "EXPLICITLY_SELECTED_APPROVED_POLICIES";
  summary: { checked_policies: number; urgent: number; replenish: number; no_reorder: number; unknown: number; review_opportunities: number };
  items: CurrentPolicyCheck[]; freshness_rechecked: true; live_sync_performed: false; saved: false; stock_reserved: false; purchasing_eligible: false;
}
export interface CurrentRequest { choices: ComparisonChoice[] }
export function useCurrentChoices(page: number, q: string, product: string) {
  return useQuery({ queryKey: ["replenishment-now", "choices", page, q, product], queryFn: () => api.get<Page<CurrentPolicyChoice>>("/replenishment-now/policies", { page, page_size: 25, q, product_id: product }) });
}
export const urgencyLabel: Record<Urgency, string> = { URGENT: "Urgent at check", REPLENISH: "Replenishment shortfall", NO_REORDER: "No reorder indicated", UNKNOWN: "Current inputs unknown" };
export const opportunityLabel: Record<Opportunity, string> = { REVIEW_OPPORTUNITY: "Supplier review opportunity", UNAVAILABLE: "Supplier opportunity unavailable", UNCONFIRMED: "Supplier review incomplete", NO_SHORTFALL: "No shortfall" };
