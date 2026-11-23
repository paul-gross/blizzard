// bzh:frontend-domain-boundary — the cadence as the hub reads it and as its forms edit it; no wire, no Angular.
import type { Vocabulary } from './vocabulary.domain';

/** What a cadence's spacing counts from. */
export type CadenceAnchor = 'scheduled' | 'started' | 'finished';

/** The anchors a spacing may count from, in the order a form offers them. */
export const CADENCE_ANCHORS: readonly CadenceAnchor[] = ['scheduled', 'started', 'finished'];

/** A chore's run mode. */
export type ChoreMode = 'full' | 'delta';

/** Why attention stops a cadence's errands. */
export type CadenceAttentionReason =
  | 'routine_unknown'
  | 'routine_retired'
  | 'graph_unresolved'
  | 'scope_unknown'
  | 'scope_retired'
  | 'scope_unlinked'
  | 'no_scopes'
  | 'no_chores';

/** A cadence's cron and the zone it reads in. */
export interface CadenceSchedule {
  readonly cron: string;
  readonly zone: string;
}

/** One chore a cadence keeps — every field an edit of the chore list resends, so an untouched chore resends as read. */
export interface Chore {
  readonly id: string;
  readonly key: string;
  /** The routine's slug. */
  readonly routine: string;
  /** Every linked scope, the listed scopes, or `null` for the routine's default scope. */
  readonly scopes: 'all' | readonly string[] | null;
  readonly mode: Vocabulary<ChoreMode>;
  readonly promote: boolean;
  readonly note: string | null;
  /** An urgency slug, or `null` for the default. */
  readonly urgency: string | null;
  readonly scopesSetAt: string;
}

/** One thing stopping a cadence's errands: the chore (`null` for the whole cadence), the scope, and why. */
export interface CadenceAttention {
  readonly chore: string | null;
  readonly scope: string | null;
  readonly reason: Vocabulary<CadenceAttentionReason>;
}

/** A cadence's errands counted by state. */
export interface CadenceErrandCounts {
  readonly due: number;
  readonly running: number;
  readonly waiting: number;
  readonly stopped: number;
}

/** A cadence as the hub reads it, its iteration included. */
export interface Cadence {
  readonly id: string;
  readonly revision: number;
  readonly slug: string;
  readonly name: string;
  readonly projectId: string | null;
  readonly projectName: string | null;
  readonly projectSlug: string | null;
  readonly schedule: CadenceSchedule | null;
  readonly spacingMinutes: number | null;
  /** What the spacing counts from; `null` when unstated, read as `started`. */
  readonly anchor: Vocabulary<CadenceAnchor> | null;
  readonly chores: readonly Chore[];
  readonly attention: readonly CadenceAttention[];
  readonly errandCounts: CadenceErrandCounts;
  /** The next moments the cadence fires, soonest first. */
  readonly nextMoments: readonly string[];
  readonly retired: boolean;
  readonly createdAt: string;
}

/** A cadence as its form states it, every field as typed. A blank field is left unstated. */
export interface CadenceDraft {
  readonly slug: string;
  readonly name: string;
  readonly cron: string;
  readonly zone: string;
  /** As typed: a whole number and `m`, `h`, or `d` — `10d`. */
  readonly spacing: string;
  readonly anchor: CadenceAnchor | 'unstated';
}

/** An empty cadence draft: no slug, no name, no rhythm. */
export const EMPTY_CADENCE_DRAFT: CadenceDraft = {
  slug: '',
  name: '',
  cron: '',
  zone: '',
  spacing: '',
  anchor: 'unstated',
};

/** A chore as its form states it, every field as typed. */
export interface ChoreDraft {
  readonly key: string;
  /** The routine's slug. */
  readonly routine: string;
  /** `all`, a comma- or space-separated list of scope slugs, or blank for the routine's default scope. */
  readonly scopes: string;
  /** The run mode — kept verbatim, a mode this build does not know included. */
  readonly mode: string;
  readonly promote: boolean;
  readonly note: string;
  /** An urgency slug; `''` is the routine's default. */
  readonly urgency: string;
}

/** The empty chore a new one opens on. */
export const EMPTY_CHORE_DRAFT: ChoreDraft = {
  key: '',
  routine: '',
  scopes: 'all',
  mode: 'full',
  promote: true,
  note: '',
  urgency: '',
};

/** Where one errand stands: due now, running, waiting for its next moment, or stopped by attention. */
export type ErrandState = 'due' | 'running' | 'waiting' | 'stopped';

/** The last run an errand minted: its chunk, once minted, and when it was scheduled. */
export interface ErrandRun {
  readonly chunkId: string | null;
  readonly scheduledAt: string;
}

/** One chore over one scope, as a cadence keeps it: its state, what stops it, its last run, and when it is next due. */
export interface CadenceErrand {
  readonly chore: string;
  readonly scope: string;
  readonly state: Vocabulary<ErrandState>;
  readonly attention: readonly CadenceAttention[];
  readonly lastRun: ErrandRun | null;
  readonly dueAt: string | null;
}
