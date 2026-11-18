"use client";

import Link from "next/link";
import { ErrorNote, Loading, Note, fmtDate } from "@/components/ui";
import { quantity } from "@/lib/recommendations";
import { useReviewSummary } from "@/lib/replenishment-review";

export function ReplenishmentOverview() {
  const query = useReviewSummary(), data = query.data;
  return <section className="card replenishment-overview" data-testid="replenishment-overview"><div className="card__header"><h2 className="card__title">Saved replenishment review</h2><Link href="/vendor-comparisons">Compare vendors</Link></div>
    <Note>These counts use the latest saved evaluation per policy. Current policy approval does not verify today’s mappings, stock, prices or shipment arrivals. Run a fresh preview before acting.</Note>
    <ErrorNote error={query.error} />{query.isPending ? <Loading what="Loading saved review counts" /> : null}
    {data ? <><div className="counters counters--tiles">
      <Link className="counter counter--tile" href="/replenishment-review?shortfall=1"><span className="counter__value">{quantity(data.current_approval_positive_shortfall)}</span><span className="counter__label">saved positive shortfalls · current approval</span></Link>
      <Link className="counter counter--tile" href="/replenishment-review?status=BLOCKED"><span className="counter__value">{quantity(data.current_approval_blocked)}</span><span className="counter__label">saved blocked evaluations · current approval</span></Link>
      <Link className="counter counter--tile" href="/replenishment-review"><span className="counter__value">{quantity(data.current_approval_total)}</span><span className="counter__label">latest saved evaluations · current approval</span></Link>
      <Link className="counter counter--tile" href="/replenishment-review?approval=CHANGED"><span className="counter__value">{quantity(data.approval_changed_total)}</span><span className="counter__label">saved evaluations with changed approval</span></Link>
    </div><p className="small muted">{quantity(data.latest_saved_total)} latest saved records in total · Counts generated {fmtDate(data.generated_at)}</p>
    {data.latest_saved_total === 0 ? <p>No saved evaluations yet. Choose an approved policy on the <Link href="/replenishment-recommendations">Recommendations page</Link> to start a preview.</p> : null}</> : null}
  </section>;
}
