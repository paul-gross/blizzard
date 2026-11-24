import type { UnknownHostValue } from 'fleet';

import type { PauseState } from './pause-state.domain';
import type { Throttle } from './throttle.domain';

/** A runner's lifecycle on its host; `unknown` is one this build cannot name, read as not serving. */
export type HostRunnerState = 'serving' | 'halted' | 'starting' | 'start_failed' | 'retired' | 'unknown';

export type KnownStartStep =
  | 'seed'
  | 'compose'
  | 'worker_shell'
  | 'tracing'
  | 'worker_scratch'
  | 'resume'
  | 'takeovers'
  | 'sessions'
  | 'serve';
/** The start step a runner failed at; `UnknownHostValue` carries one this build cannot name. */
export type StartStep = KnownStartStep | UnknownHostValue;

/** Why a runner failed to start: the step and its error. */
export interface StartFailure {
  readonly step: StartStep;
  readonly error: string;
}

/** One project-workspace binding a runner declares, with its agents. */
export interface HostRunnerBinding {
  readonly project: string;
  readonly workspace_id: string;
  readonly agents: number;
}

/** A runner the host serves or holds halted. `served` is the host's judgment that its routes answer — true even
 * for a runner halted by its hub's refusal — and is distinct from `state`. */
export interface HostRunner {
  readonly runner_id: string | null;
  readonly name: string;
  readonly state: HostRunnerState;
  readonly served: boolean;
  readonly agents_in_use: number;
  readonly max_agents: number;
  readonly pause: PauseState | null;
  readonly bindings: readonly HostRunnerBinding[];
  readonly halted_reasons: readonly string[];
  readonly start_failure: StartFailure | null;
  readonly throttle: Throttle | null;
}
