import { openLookup } from 'fleet/core';
import { type Tone } from 'fleet/kit';

import type { CadenceAttentionReason, ChoreMode, ErrandState } from '../core/cadence.domain';

/** An errand state's label and badge tone. */
export interface ErrandStateLook {
  readonly label: string;
  readonly tone: Tone;
}

/**
 * An errand's state → its label and tone: `due` and `running` read live, `waiting` parked until its next moment,
 * `stopped` the alarm. A state this build does not know renders raw in the idle tone.
 */
export const errandStateLook = openLookup<ErrandState, ErrandStateLook>(
  {
    due: { label: 'due', tone: 'spawning' },
    running: { label: 'running', tone: 'running' },
    waiting: { label: 'waiting', tone: 'waiting' },
    stopped: { label: 'stopped', tone: 'stale' },
  },
  (raw) => ({ label: raw, tone: 'idle' }),
);

/** The errand states in the order a cadence's counts read them. */
export const ERRAND_STATES: readonly ErrandState[] = ['due', 'running', 'waiting', 'stopped'];

/** Why attention stops an errand, as a person reads it. A reason this build does not know renders raw. */
export const attentionReasonLabel = openLookup<CadenceAttentionReason, string>(
  {
    routine_unknown: 'its routine does not exist',
    routine_retired: 'its routine is retired',
    graph_unresolved: "its routine's graph does not resolve",
    scope_unknown: 'the scope does not exist',
    scope_retired: 'the scope is retired',
    scope_unlinked: 'the scope is not linked to the routine',
    no_scopes: 'no scope to run over',
    no_chores: 'the cadence keeps no chore',
  },
  (raw) => raw,
);

/** A chore's run mode, as a person reads it. A mode this build does not know renders raw. */
export const choreModeLabel = openLookup<ChoreMode, string>({ full: 'full', delta: 'delta' }, (raw) => raw);
