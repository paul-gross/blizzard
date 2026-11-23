// bzh:frontend-domain-boundary — what the routine detail reads about one routine over a window: its finding trend, its
// sweeps, and its proposal counts; no wire, no Angular.
import type { ProposalOrigin } from './proposal.domain';
import type { Vocabulary } from './vocabulary.domain';

/** One period of a routine's finding trend: findings created, the outflow, and the withdrawn and reopened. */
export interface RoutineTrendPeriod {
  readonly created: number;
  readonly outflow: number;
  readonly withdrawn: number;
  readonly reopened: number;
}

/** A routine's finding inflow against outflow over a window, period by period. */
export interface RoutineTrend {
  readonly periods: readonly RoutineTrendPeriod[];
}

/** One measurement a sweep produced on a scope. */
export interface MeasurementReading {
  readonly scopeSlug: string;
  readonly producedAt: string;
  readonly measurement: string;
}

/** A related scope's last sweep: the set it produced and the revision each repo was swept at; both `null` until swept. */
export interface ScopeLastSwept {
  readonly scopeSlug: string;
  readonly findingSetId: string | null;
  readonly producedAt: string | null;
  readonly revisions: Readonly<Record<string, string>>;
}

/** A routine's sweeps: each related scope's last sweep (unwindowed) and the window's measurement readings. */
export interface RoutineSweeps {
  readonly lastSwept: readonly ScopeLastSwept[];
  readonly measurements: readonly MeasurementReading[];
}

/** One origin and class's proposal counts over a window, and how each closed. */
export interface ProposalCountsRow {
  readonly origin: Vocabulary<ProposalOrigin>;
  readonly proposalClass: string;
  readonly created: number;
  readonly open: number;
  readonly passed: number;
  readonly acceptedWithItem: number;
  readonly acceptedWithoutItem: number;
}

/** A routine's proposal counts over a window, one row per origin and class. */
export interface RoutineProposalCounts {
  readonly rows: readonly ProposalCountsRow[];
}
