"use client";

import Link from "next/link";
import { KeyValues, Note, StatusBadge, fmtDate } from "@/components/ui";
import { financialNumber } from "@/lib/financial-imports";
import { policyReason } from "@/lib/purchasing-policies";
import { exactDisplay, hasUnsafeQuantity, quantity } from "@/lib/recommendations";
import type { EvaluationSnapshot } from "@/lib/recommendations";

export function Reasons({ codes }: { codes: string[] }) { return codes.length ? <ul>{[...new Set(codes)].map(code => <li key={code}>{policyReason(code)}</li>)}</ul> : <p className="muted">None recorded.</p>; }
export function Evidence({ snapshot: s }: { snapshot: EvaluationSnapshot }) {
  const f = s.financial;
  return <div data-testid="evaluation-evidence">
    <section className="card"><h2>Evaluation evidence</h2><Note>Historical evaluation for staff review. Positive quantities require further purchasing checks.</Note>
      {hasUnsafeQuantity(s) ? <Note tone="warn">Some quantities are outside the exact display range. Recording from this screen is unavailable.</Note> : null}
      <KeyValues rows={[["Result", <StatusBadge key="status" value={s.status} />], ["Evaluated", fmtDate(s.evaluated_at)], ["Approved policy", <Link key="policy" href={"/purchasing-policies?policy=" + encodeURIComponent(s.policy_id)}>Version {s.approved_policy.revision_number}</Link>], ["Demand period (end exclusive)", fmtDate(s.readiness.demand_window_start) + " – " + fmtDate(s.readiness.demand_window_end)], ["Amazon evidence", s.readiness.status], ["Supplier evidence", s.supplier_inputs], ["Profitability", f ? "Historical report context only" : "Not integrated"]]} />
      <h3>Blocking reasons</h3><Reasons codes={[...s.policy_blocking_reasons, ...s.readiness.issues.map(issue => issue.code)]} />
      <h3>Further review</h3><Reasons codes={s.review_reasons ?? []} />
    </section>
    {s.pools.map(pool => <section className="card" key={pool.pool_id}><h2>Stock pool · {pool.pool_id}</h2><StatusBadge value={pool.result.status} />
      <KeyValues rows={[["Sales (base units)", quantity(pool.inputs.sales_units)], ["On hand", quantity(pool.inputs.on_hand_units)], ["Usable inbound", quantity(pool.inputs.inbound_units)], ["Supplier stock", quantity(pool.inputs.vendor_stock_units)], ["Supplier availability", pool.inputs.vendor_availability], ["Daily sales", financialNumber(pool.result.calculation.daily_sales, false)], ["Days of supply", financialNumber(pool.result.calculation.days_of_supply, false)], ["Coverage days", quantity(pool.result.calculation.coverage_days)], ["Required units", quantity(pool.result.calculation.required_units)], ["Net shortfall", quantity(pool.result.calculation.net_shortfall_units)], ["Rounded quantity before supplier cap", quantity(pool.result.calculation.order_quantity_before_vendor_cap)], ["Suggested base units", quantity(pool.result.suggested_units)]]} />
      <Reasons codes={pool.blocking_reasons} />
    </section>)}
    {s.supplier ? <section className="card"><h2>Supplier source evidence</h2><pre className="json-block">{JSON.stringify(exactDisplay(s.supplier), null, 2)}</pre></section> : null}
    {f ? <section className="card" data-testid="financial-context"><h2>Historical financial context</h2><Note tone="warn">These report amounts are historical. They are not current unit costs or projected profit on suggested quantities. Unallocated amounts stay separate.</Note>
      <KeyValues rows={[["Report", <Link key="report" href={"/financial-imports?import=" + encodeURIComponent(f.import_id)}>{f.original_filename}</Link>], ["Store", f.store_name], ["Currency", f.currency], ["Reporting period (end exclusive)", fmtDate(f.period_start) + " – " + fmtDate(f.period_end)], ["Reporting timezone", f.profile_snapshot.reporting_timezone ?? "Unknown"], ["Received", fmtDate(f.received_at)], ["Bindings", f.binding_revision_id ?? "Original bindings"], ["Context status", <StatusBadge key="context" value={f.status} />], ["Unallocated rows", quantity(f.unallocated_rows)]]} />
      <Reasons codes={f.review_reasons} />
      <div className="table-wrap" role="region" aria-label="Historical SKU financial amounts" tabIndex={0}><table className="table"><thead><tr><th>Amazon SKU</th><th>Included</th><th>Net sales USD</th><th>Fees USD</th><th>Cost USD</th><th>Advertising USD</th><th>Reported profit USD</th><th>Computed profit USD</th><th>Margin %</th><th>ROI %</th></tr></thead><tbody>{f.rows.map(row => <tr key={row.listing_id}><td>{row.seller_sku}<div className="small">Source row {quantity(row.source_row_number)}</div><Reasons codes={row.review_reasons} /></td><td>{row.included_in_totals ? "Yes" : "Excluded / unknown"}</td>{["net_sales", "fees", "cost", "ad_spend", "profit"].map(key => <td key={key}>{financialNumber(row.amounts[key])}</td>)}<td>{financialNumber(row.computed_profit)}</td><td>{financialNumber(row.computed_margin_percent, false)}</td><td>{financialNumber(row.computed_roi_percent, false)}</td></tr>)}</tbody></table></div>
      <h3>Complete product listing totals · USD</h3>{f.totals ? <KeyValues rows={Object.entries(f.totals).map(([key, value]) => [key.replaceAll("_", " "), financialNumber(value)])} /> : <Note tone="warn">Unknown: the complete listing set has not been verified. Partial amounts are not presented as product totals.</Note>}
      <h3>Source reconciliation controls</h3><div className="table-wrap" role="region" aria-label="Source financial controls" tabIndex={0}><table className="table"><thead><tr><th>Field</th><th>Detail total</th><th>Reported total</th><th>Difference</th><th>Status</th></tr></thead><tbody>{f.controls.map(control => <tr key={control.field}><td>{control.field}</td><td>{financialNumber(control.detail_total, false)}</td><td>{financialNumber(control.reported_total, false)}</td><td>{financialNumber(control.difference, false)}</td><td><StatusBadge value={control.status} /></td></tr>)}</tbody></table></div>
    </section> : null}
    <details className="card"><summary>Complete immutable input and result snapshot</summary><pre className="json-block">{JSON.stringify(exactDisplay(s), null, 2)}</pre></details>
  </div>;
}
