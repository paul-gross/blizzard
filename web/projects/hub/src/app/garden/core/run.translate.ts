// bzh:frontend-domain-boundary — the one place a routine run's wire is written, its refusals read, and its run reads
// translated; an unforeseen run mode becomes an `Unlisted` here (bzh:frontend-open-vocabulary).
import {
  hubApi,
  openLookup,
  type RefusalMap,
  type RoutineBaselineView,
  type RoutineRunResponse,
  type RunCadenceView,
  type RunDeltaView,
  RunMode as WireRunMode,
  type RunRowView,
} from 'fleet';
import { laneStatus } from 'fleet/shell';

import type {
  RoutineBaseline,
  RunCadence,
  RunDelta,
  RunDeltaSet,
  RunDraft,
  RunEscalation,
  RunMode,
  RunResponse,
  RunRow,
} from './run.domain';
import type { Vocabulary } from './vocabulary.domain';

/** What a run is sent as: the scope, the mode, the trimmed note, and an urgency only when one is chosen. */
export interface RunRequest {
  readonly scopeSlug: string;
  readonly mode: WireRunMode;
  readonly note: string | null;
  readonly urgency?: string;
}

/** A draft as the run request: a mode other than `delta` is a full run, a blank note is none, and a blank urgency
 * is left to the routine's default. */
export function toRunRequest(draft: RunDraft): RunRequest {
  return {
    scopeSlug: draft.scope,
    mode: draft.mode === 'delta' ? WireRunMode.DELTA : WireRunMode.FULL,
    note: draft.note.trim() || null,
    ...(draft.urgency ? { urgency: draft.urgency } : {}),
  };
}

const RUN_REFUSAL_FIELDS: Readonly<Record<string, keyof RunDraft>> = {
  scope_slug: 'scope',
  mode: 'mode',
  urgency: 'urgency',
  note: 'note',
};

/** How the hub's refusals of a run reach the draft: its own fields on theirs, anything else on the form line. */
export const RUN_REFUSALS: RefusalMap<RunDraft> = {
  field: (path) => (Object.hasOwn(RUN_REFUSAL_FIELDS, path) ? RUN_REFUSAL_FIELDS[path] : null),
  kinds: {},
};

const runModeOf = openLookup<WireRunMode, Vocabulary<RunMode>>(
  { [WireRunMode.FULL]: 'full', [WireRunMode.DELTA]: 'delta' },
  (raw) => ({ unlisted: raw }),
);

function escalationOf(wire: hubApi.RunEscalationView | null): RunEscalation | null {
  return wire === null
    ? null
    : { nodeName: wire.node_name, takeoverCommand: wire.takeover_command, wrappedTakeoverCommand: wire.wrapped_takeover_command };
}

function cadenceOf(wire: RunCadenceView | null): RunCadence | null {
  return wire === null ? null : { slug: wire.cadence_slug, name: wire.cadence_name, choreKey: wire.chore_key };
}

/** A run-list row as the domain holds it; its outcome read as a lane status. */
export function fromRunRowView(wire: RunRowView): RunRow {
  return {
    chunkId: wire.chunk_id,
    routineName: wire.routine_name,
    scopeSlug: wire.scope_slug,
    mode: runModeOf(wire.mode),
    mintedAt: wire.minted_at,
    laneStatus: laneStatus(wire.outcome),
    escalation: escalationOf(wire.escalation),
    delivered: wire.delivered.map((set) => ({
      addedCount: set.added_count,
      observedCount: set.observed_count,
      goneCount: set.gone_count,
    })),
  };
}

function deltaSetOf(wire: hubApi.DeliveredSetDeltaView): RunDeltaSet {
  return {
    findingSetId: wire.finding_set_id,
    revisions: { ...wire.revisions },
    measurement: wire.measurement,
    added: wire.added.map((a) => ({
      findingId: a.finding_id,
      findingClass: a.class,
      locus: a.locus,
      summary: a.summary,
      introduced: a.introduced,
    })),
    observed: wire.observed.map((o) => ({ findingId: o.finding_id, findingClass: o.class, locus: o.locus, summary: o.summary })),
    gone: wire.gone.map((g) => ({ findingId: g.finding_id, note: g.note })),
  };
}

/** One run's detail as the domain holds it. */
export function fromRunDeltaView(wire: RunDeltaView): RunDelta {
  return {
    chunkId: wire.chunk_id,
    routineName: wire.routine_name,
    scopeSlug: wire.scope_slug,
    escalation: escalationOf(wire.escalation),
    cadence: cadenceOf(wire.cadence),
    sets: wire.sets.map(deltaSetOf),
  };
}

/** A routine's baseline for one scope, as the domain holds it. */
export function fromRoutineBaselineView(wire: RoutineBaselineView): RoutineBaseline {
  return {
    scopeSlug: wire.scope_slug,
    findingSetId: wire.finding_set_id,
    recordedAt: wire.recorded_at,
    repos: wire.repos.map((r) => ({ repo: r.repo, revision: r.revision, landedSince: r.landed_since })),
  };
}

/** The run the hub minted, as the domain holds it. */
export function fromRoutineRunResponse(wire: RoutineRunResponse): RunResponse {
  return { chunkId: wire.chunk_id, downgraded: wire.downgraded };
}
