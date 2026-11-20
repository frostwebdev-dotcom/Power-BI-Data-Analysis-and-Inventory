"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Page } from "./api";
import { policyPath } from "./purchasing-policies";
export interface InboundSelection { revision_ids: string[] }
export interface Arrival { reference: string; base_units: number; expected_at: string }
export interface InboundPosition { declared_total_base_units: number; arrivals: Arrival[]; observed_at: string; expires_at: string; evidence_reference: string; complete_position_confirmed: boolean; no_on_hand_overlap_confirmed: boolean }
export interface InboundRevision { id: string; policy_id: string; approved_revision_id: string; pool_id: string; channel: "FBA" | "FBM"; revision_number: number; previous_revision_id: string | null; kind: "CONFIRMED" | "REVOKED"; inventory_run_id: string; stock_snapshot_id: string; source_context: Record<string, unknown>; source_sha256: string; position: InboundPosition; recorded_by_user_id: string; recorded_at: string; reason: string; purchasing_eligible: false }
export interface InboundContext { policy_id: string; approved_revision_id: string; pool_id: string; channel: "FBA" | "FBM"; evaluated_at: string; head_id: string | null; source_context: Record<string, unknown>; source_sha256: string; blockers: string[] }
export interface InboundProposal { expected_approved_revision_id: string; pool_id: string; expected_head_id: string | null; expected_source_sha256: string; position: InboundPosition }
export interface InboundPreview { context: InboundContext; proposal: InboundProposal; can_confirm: boolean; blockers: string[]; preview_sha256: string }
export interface InboundRecord extends InboundProposal { request_id: string; expected_preview_sha256: string; reason: string; acknowledge_review: true }
export interface InboundRevoke { request_id: string; expected_head_id: string; reason: string; acknowledge_review: true }
export interface InboundAssessment { revision: InboundRevision; basis: "MANAGER_REVIEWED_COMPLETE_POSITION"; status: "APPLIED" | "BLOCKED"; source_capture_at: string | null; usable_base_units: number | null; arrivals: (Arrival & { disposition: string; credited_units: number | null })[]; reasons: string[] }
export const inboundPath = (id: string) => policyPath(id) + "/inbound";
export function useInbound(policy: string, page: number, enabled = true, headsOnly = true) {
  return useQuery({ queryKey: ["inbound", policy, page, headsOnly], enabled: enabled && Boolean(policy), queryFn: () => api.get<Page<InboundRevision>>(inboundPath(policy), { page, page_size: 25, heads_only: headsOnly }) });
}
