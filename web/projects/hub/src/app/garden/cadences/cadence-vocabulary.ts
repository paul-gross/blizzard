import { AttentionReason, CadenceAnchor, ErrandState, openLookup, RunMode, type Tone } from 'fleet';

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
    [ErrandState.DUE]: { label: 'due', tone: 'spawning' },
    [ErrandState.RUNNING]: { label: 'running', tone: 'running' },
    [ErrandState.WAITING]: { label: 'waiting', tone: 'waiting' },
    [ErrandState.STOPPED]: { label: 'stopped', tone: 'stale' },
  },
  (raw) => ({ label: raw, tone: 'idle' }),
);

/** The errand states in the order a cadence's counts read them. */
export const ERRAND_STATES: readonly ErrandState[] = [
  ErrandState.DUE,
  ErrandState.RUNNING,
  ErrandState.WAITING,
  ErrandState.STOPPED,
];

/** Why attention stops an errand, as a person reads it. A reason this build does not know renders raw. */
export const attentionReasonLabel = openLookup<AttentionReason, string>(
  {
    [AttentionReason.ROUTINE_UNKNOWN]: 'its routine does not exist',
    [AttentionReason.ROUTINE_RETIRED]: 'its routine is retired',
    [AttentionReason.GRAPH_UNRESOLVED]: "its routine's graph does not resolve",
    [AttentionReason.SCOPE_UNKNOWN]: 'the scope does not exist',
    [AttentionReason.SCOPE_RETIRED]: 'the scope is retired',
    [AttentionReason.SCOPE_UNLINKED]: 'the scope is not linked to the routine',
    [AttentionReason.NO_SCOPES]: 'no scope to run over',
    [AttentionReason.NO_CHORES]: 'the cadence keeps no chore',
  },
  (raw) => raw,
);

/** A chore's run mode, as a person reads it. A mode this build does not know renders raw. */
export const choreModeLabel = openLookup<RunMode, string>(
  { [RunMode.FULL]: 'full', [RunMode.DELTA]: 'delta' },
  (raw) => raw,
);

/** The anchors a spacing may count from; an empty value leaves it unstated, read as `started`. */
export const CADENCE_ANCHORS: readonly CadenceAnchor[] = Object.values(CadenceAnchor);
