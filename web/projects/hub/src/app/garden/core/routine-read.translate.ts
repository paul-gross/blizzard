// bzh:frontend-domain-boundary — the one place the routine trend, sweeps, and proposal-counts wire is read; an
// unforeseen proposal origin becomes an `Unlisted` here (bzh:frontend-open-vocabulary).
import type { GardenProposalCountsView, GardenSweepsView, TrendView } from 'fleet/hub-api';

import { proposalOriginOf } from './proposal.translate';
import type { RoutineProposalCounts, RoutineSweeps, RoutineTrend } from './routine-read.domain';

/** A routine's trend as the domain holds it. */
export function fromTrendView(wire: TrendView): RoutineTrend {
  return {
    periods: wire.periods.map((p) => ({ created: p.created, outflow: p.outflow, withdrawn: p.withdrawn, reopened: p.reopened })),
  };
}

/** A routine's sweeps as the domain holds them. */
export function fromGardenSweepsView(wire: GardenSweepsView): RoutineSweeps {
  return {
    lastSwept: wire.last_swept.map((row) => ({
      scopeSlug: row.scope_slug,
      findingSetId: row.finding_set_id,
      producedAt: row.produced_at,
      revisions: { ...row.revisions },
    })),
    measurements: wire.measurements.map((m) => ({ scopeSlug: m.scope_slug, producedAt: m.produced_at, measurement: m.measurement })),
  };
}

/** A routine's proposal counts as the domain holds them. */
export function fromGardenProposalCountsView(wire: GardenProposalCountsView): RoutineProposalCounts {
  return {
    rows: wire.rows.map((row) => ({
      origin: proposalOriginOf(row.origin),
      proposalClass: row.class,
      created: row.created,
      open: row.open,
      passed: row.passed,
      acceptedWithItem: row.accepted_with_item,
      acceptedWithoutItem: row.accepted_without_item,
    })),
  };
}
