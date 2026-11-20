"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";

import { ErrorNote, KeyValues, Note } from "@/components/ui";
import { api } from "@/lib/api";
import { policyError, policyPath, policyReason } from "@/lib/purchasing-policies";
import type { PolicyReview, PolicySummary, ReviewAction } from "@/lib/purchasing-policies";
import type { MarketplaceListing } from "@/lib/types";
import { DefinitionView, MappingEvidence, reviewListings } from "./definition";
import { Field } from "./editor";

export function PolicyReviewPanel({ policy, listings }: { policy: PolicySummary; listings: MarketplaceListing[] }) {
  const cache = useQueryClient();
  const [preview, setPreview] = useState<PolicyReview | null>(null);
  const [reason, setReason] = useState(""), [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [message, setMessage] = useState("");
  function reset() { setPreview(null); setReason(""); setAcknowledged(false); }
  const inspect = useMutation({
    mutationFn: (action: ReviewAction) => api.post<PolicyReview>(policyPath(policy.policy_id) + "/review-preview?action=" + action),
    onMutate: () => { reset(); setError(null); setMessage(""); },
    onSuccess: value => {
      if (value.expected_head_id !== policy.head.id) { setError(new Error("The policy changed. Reload the policy before reviewing its latest version.")); void cache.invalidateQueries({ queryKey: ["purchasing-policies", policy.policy_id] }); return; }
      setPreview(value);
    },
    onError: issue => setError(policyError(issue)),
  });
  const record = useMutation({
    mutationFn: (value: PolicyReview) => api.post<PolicySummary>(policyPath(policy.policy_id) + "/reviews", {
      action: value.action, expected_head_id: value.expected_head_id, reviewed_revision_id: value.reviewed_revision_id,
      definition_sha256: value.definition_sha256, context_sha256: value.context_sha256, reason: reason.trim(), acknowledge_review: acknowledged,
    }),
    onSuccess: value => { setMessage(value.head.kind === "APPROVED" ? "Policy approval recorded. Current source data still needs verification before recommendations." : "Policy approval revoked. Previous versions remain in history."); reset(); cache.setQueryData(["purchasing-policies", policy.policy_id], value); void cache.invalidateQueries({ queryKey: ["purchasing-policies"] }); },
    onError: issue => { setError(policyError(issue)); reset(); void cache.invalidateQueries({ queryKey: ["purchasing-policies", policy.policy_id] }); },
  });
  const currentPreview = preview?.expected_head_id === policy.head.id ? preview : null;
  const busy = inspect.isPending || record.isPending;
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (currentPreview?.review_allowed && acknowledged && reason.trim().length >= 10 && !busy) { setError(null); record.mutate(currentPreview); } }
  return <section className="card" data-testid="policy-review-panel">
    <h2>Manager review</h2><p className="muted">Review the latest saved draft or revoke the current approval. Selecting an older history entry does not change either target.</p>
    <div className="policy-actions">
      <button type="button" className="button" disabled={busy || policy.head.kind !== "DRAFT"} onClick={() => inspect.mutate("APPROVE")}>Preview approval of version {policy.head.revision_number}</button>
      {policy.approved ? <button type="button" className="button" disabled={busy} onClick={() => inspect.mutate("REVOKE")}>Preview revocation of version {policy.approved.revision_number}</button> : null}
    </div>
    <ErrorNote error={error} />{message ? <p role="status" className="note note--ok">{message}</p> : null}
    {currentPreview ? <div className="policy-review" data-testid="policy-review-preview">
      <h3>{currentPreview.action === "APPROVE" ? "Review saved draft for approval" : "Review current approval for revocation"}</h3>
      <p>{currentPreview.action === "REVOKE" ? `This revokes approved version ${policy.approved?.revision_number}, even when a newer draft exists.` : `This reviews saved draft version ${policy.head.revision_number}.`}</p>
      <DefinitionView definition={currentPreview.definition} listings={[...reviewListings(currentPreview.review_context), ...listings]} />
      <MappingEvidence context={currentPreview.review_context} />
      <details><summary>Version and review verification</summary><KeyValues rows={[["Reviewed version reference", currentPreview.reviewed_revision_id], ["Editing head reference", currentPreview.expected_head_id], ["Definition verification", currentPreview.definition_sha256], ["Identity evidence verification", currentPreview.context_sha256]]} /></details>
      {currentPreview.blocking_reasons.length ? <Note tone="warn"><strong>Approval cannot be recorded</strong><ul>{currentPreview.blocking_reasons.map(code => <li key={code}>{policyReason(code)}</li>)}</ul></Note> : null}
      {currentPreview.review_allowed ? <form onSubmit={submit}><fieldset disabled={busy}>
        <Field label="Reason for manager decision" hint="10–1000 characters. Record why this definition should be approved or its approval revoked."><textarea required minLength={10} maxLength={1000} rows={3} value={reason} onChange={e => setReason(e.target.value)} /></Field>
        <label className="check"><input type="checkbox" checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)} />I reviewed this complete saved definition and its current mapping evidence</label>
        <div className="policy-actions"><button type="submit" className="button button--primary" disabled={!acknowledged || reason.trim().length < 10}>{record.isPending ? "Recording…" : currentPreview.action === "APPROVE" ? "Record policy approval" : "Revoke policy approval"}</button><button type="button" className="button" onClick={reset}>Cancel review</button></div>
      </fieldset></form> : <button className="button" type="button" onClick={reset}>Close review</button>}
    </div> : null}
  </section>;
}
