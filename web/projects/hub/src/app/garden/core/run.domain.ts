// bzh:frontend-domain-boundary — a routine run as its form states it and as the hub reads it back; no wire, no Angular.
import type { LaneStatus } from 'fleet';

import type { Vocabulary } from './vocabulary.domain';

/** The modes a run offers, in the order the form lists them. */
export const RUN_MODES = ['full', 'delta'] as const;

/** How a run settles its delta baseline: `full` reports every finding, `delta` only what changed since the baseline. */
export type RunMode = (typeof RUN_MODES)[number];

/** A routine run as its form states it, every field as typed. */
export interface RunDraft {
  /** The chosen scope's slug; `''` until one is chosen. */
  readonly scope: string;
  /** `full`, or `delta` once the routine has a baseline for the scope. */
  readonly mode: string;
  /** An urgency slug; `''` is the routine's default. */
  readonly urgency: string;
  /** A note folded into the run's charge; blank is left unstated. */
  readonly note: string;
}

/** The run a form opens on: no scope yet, a full sweep at the routine's default urgency. */
export const EMPTY_RUN_DRAFT: RunDraft = { scope: '', mode: 'full', urgency: '', note: '' };

/** A run's open escalation: the node that raised it and the commands that take it over. */
export interface RunEscalation {
  readonly nodeName: string | null;
  readonly takeoverCommand: string;
  readonly wrappedTakeoverCommand: string;
}

/** The cadence chore that minted a run; `choreKey` is `null` once the chore has left the cadence. */
export interface RunCadence {
  readonly slug: string;
  readonly name: string;
  readonly choreKey: string | null;
}

/** One finding set a run delivered, counted: what it added, re-observed, and found gone. */
export interface DeliveredSetCounts {
  readonly addedCount: number;
  readonly observedCount: number;
  readonly goneCount: number;
}

/** One routine run as the run list reads it, in the hub's order. */
export interface RunRow {
  readonly chunkId: string;
  /** The routine's slug. */
  readonly routineSlug: string;
  readonly scopeSlug: string;
  readonly mode: Vocabulary<RunMode>;
  readonly mintedAt: string;
  /** The run chunk's outcome, as a lane status. */
  readonly laneStatus: LaneStatus;
  /** `null` unless the run is escalated. */
  readonly escalation: RunEscalation | null;
  readonly delivered: readonly DeliveredSetCounts[];
}

/** A finding a delivered set added. `findingId` is `null` on a set predating the finding linkage. */
export interface RunAddedFinding {
  readonly findingId: string | null;
  readonly findingClass: string;
  readonly locus: string;
  readonly summary: string;
  readonly introduced: string | null;
}

/** A finding a delivered set re-observed; its descriptive fields are `null` when the id names no finding row. */
export interface RunObservedFinding {
  readonly findingId: string;
  readonly findingClass: string | null;
  readonly locus: string | null;
  readonly summary: string | null;
}

/** A finding a delivered set found gone. */
export interface RunGoneFinding {
  readonly findingId: string;
  readonly note: string;
}

/** One delivered set's own published delta. */
export interface RunDeltaSet {
  readonly findingSetId: string;
  /** The revision each repo was swept at. */
  readonly revisions: Readonly<Record<string, string>>;
  readonly measurement: string | null;
  readonly added: readonly RunAddedFinding[];
  readonly observed: readonly RunObservedFinding[];
  readonly gone: readonly RunGoneFinding[];
}

/** One run's detail: its identity, escalation, the cadence that minted it, and each delivered set's delta. */
export interface RunDelta {
  readonly chunkId: string;
  /** The routine's slug. */
  readonly routineSlug: string;
  readonly scopeSlug: string;
  readonly escalation: RunEscalation | null;
  /** `null` for a run started by hand. */
  readonly cadence: RunCadence | null;
  readonly sets: readonly RunDeltaSet[];
}

/** One repo a routine's baseline pinned: its revision and how many changes have landed since. */
export interface RoutineBaselineRepo {
  readonly repo: string;
  readonly revision: string;
  readonly landedSince: number;
}

/** The delta baseline a routine holds for one scope it has swept. */
export interface RoutineBaseline {
  readonly scopeSlug: string;
  readonly findingSetId: string;
  readonly recordedAt: string;
  readonly repos: readonly RoutineBaselineRepo[];
}

/** The run the hub minted: its chunk, and whether a delta run was downgraded to full for want of a baseline. */
export interface RunResponse {
  readonly chunkId: string;
  readonly downgraded: boolean;
}
