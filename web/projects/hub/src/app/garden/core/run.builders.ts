// The run domain builders specs and stories build their inputs from — domain literals only, no wire, no Angular.
// The wire builders (`runRowView` and its kin in `fleet/testing`) stay for the translator, query, and mutation specs
// and for answering a stubbed transport.
import { fixtureInstant } from 'fleet/testing';

import type {
  DeliveredSetCounts,
  RoutineBaseline,
  RunDelta,
  RunDeltaSet,
  RunEscalation,
  RunResponse,
  RunRow,
} from './run.domain';

/** A run's escalation, at the node that raised it. */
export function gardenRunEscalation(overrides: Partial<RunEscalation> = {}): RunEscalation {
  return { nodeName: 'survey', takeoverCommand: 'blizzard hub chunk takeover ch_1', wrappedTakeoverCommand: '', ...overrides };
}

/** One delivered set that added, re-observed, and found gone nothing. */
export function gardenDeliveredSet(overrides: Partial<DeliveredSetCounts> = {}): DeliveredSetCounts {
  return { addedCount: 0, observedCount: 0, goneCount: 0, ...overrides };
}

/** One run-list row: a full `nightly` sweep of `blizzard` that finished a day before the fixture clock, delivering
 * nothing. */
export function gardenRunRow(overrides: Pick<RunRow, 'chunkId'> & Partial<RunRow>): RunRow {
  return {
    routineName: 'nightly',
    scopeSlug: 'blizzard',
    mode: 'full',
    mintedAt: fixtureInstant(-24 * 3600),
    laneStatus: { kind: 'done' },
    escalation: null,
    delivered: [],
    ...overrides,
  };
}

/** One delivered set's delta, empty in every group, pinned to no revision. */
export function gardenRunDeltaSet(overrides: Pick<RunDeltaSet, 'findingSetId'> & Partial<RunDeltaSet>): RunDeltaSet {
  return { revisions: {}, measurement: null, added: [], observed: [], gone: [], ...overrides };
}

/** One run's delta: a `nightly` sweep of `blizzard`, started by hand, delivering no sets. */
export function gardenRunDelta(overrides: Pick<RunDelta, 'chunkId'> & Partial<RunDelta>): RunDelta {
  return { routineName: 'nightly', scopeSlug: 'blizzard', escalation: null, cadence: null, sets: [], ...overrides };
}

/** A routine's baseline over one scope, recorded a day before the fixture clock, pinning no repo. */
export function gardenRoutineBaseline(overrides: Pick<RoutineBaseline, 'scopeSlug'> & Partial<RoutineBaseline>): RoutineBaseline {
  return { findingSetId: 'fins_1', recordedAt: fixtureInstant(-24 * 3600), repos: [], ...overrides };
}

/** The run the hub minted, not downgraded. */
export function gardenRunResponse(overrides: Pick<RunResponse, 'chunkId'> & Partial<RunResponse>): RunResponse {
  return { downgraded: false, ...overrides };
}
