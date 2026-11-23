// bzh:frontend-domain-boundary — a finding and its fact chain as the hub reads them; no wire, no Angular.
import type { Vocabulary } from './vocabulary.domain';

/** Where a finding stands: open (`live`, or `gone`-flagged for review), or one of the states it exits into. */
export type FindingState =
  | 'live'
  | 'gone'
  | 'delivered'
  | 'resolved'
  | 'gone-confirmed'
  | 'wont-fix'
  | 'not-a-finding'
  | 'superseded';

/** Every finding state, in the order a filter offers them. */
export const FINDING_STATES: readonly FindingState[] = [
  'live',
  'gone',
  'delivered',
  'resolved',
  'gone-confirmed',
  'wont-fix',
  'not-a-finding',
  'superseded',
];

/** How a review-sourced finding weighs on the change it was raised against. */
export type FindingSeverity = 'blocking' | 'should-fix';

/** What raised a finding: a routine's sweep, or a chunk's review. */
export type FindingSource = 'routine' | 'review';

/** How a finding left the open set. */
export type FindingExit = 'outflow' | 'withdrawn';

/** What one fact in a finding's append-only chain records. */
export type FindingFactKind =
  | 'add'
  | 'observed'
  | 'gone'
  | 'delivered'
  | 'resolved'
  | 'gone-confirmed'
  | 'wont-fix'
  | 'not-a-finding'
  | 'superseded'
  | 'reopened';

/** A finding as the hub reads it. */
export interface Finding {
  readonly id: string;
  /** The finding's class — opaque, deployment-chosen vocabulary. */
  readonly findingClass: string;
  readonly locus: string;
  readonly summary: string;
  readonly note: string | null;
  readonly state: Vocabulary<FindingState>;
  /** Non-null once the finding has exited. */
  readonly exit: Vocabulary<FindingExit> | null;
  readonly observedCount: number;
  /** The git revision that introduced the finding — a revision, not a timestamp. */
  readonly introducedRev: string | null;
  /** The authored instant of {@link introducedRev}, when the hub resolved it. */
  readonly introducedAt: string | null;
  readonly firstObservedAt: string | null;
  readonly lastSeenAt: string | null;
  /** The slug of the routine that raised the finding; `null` for a review-sourced one. */
  readonly routineSlug: string | null;
  readonly scopeSlug: string;
  readonly source: Vocabulary<FindingSource>;
  /** Set only when {@link source} is `review`. */
  readonly severity: Vocabulary<FindingSeverity> | null;
  /** The chunk whose review raised the finding, set only when {@link source} is `review`. */
  readonly raisedByChunkId: string | null;
}

/** One entry in a finding's append-only fact chain. */
export interface FindingFact {
  readonly kind: Vocabulary<FindingFactKind>;
  readonly note: string | null;
  readonly actor: string | null;
  readonly recordedAt: string;
  readonly proposalId: string | null;
  readonly supersededBy: string | null;
}

/** A finding read on its own, carrying its whole fact chain oldest-first. */
export interface FindingDetail extends Finding {
  readonly facts: readonly FindingFact[];
}

/** One page of the findings triage bucket, newest first. */
export interface FindingPage {
  readonly findings: readonly Finding[];
  readonly nextCursor: string | null;
}
