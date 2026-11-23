import type { PauseState } from './pause-state.domain';

/** A runner's lifecycle on its host; `unknown` is one this build cannot name, read as not serving. */
export type HostRunnerState = 'serving' | 'halted' | 'starting' | 'start_failed' | 'retired' | 'unknown';

/** A runner the host serves or holds halted, as the panel's shell reads it. */
export interface HostRunner {
  readonly runner_id: string | null;
  readonly name: string;
  readonly state: HostRunnerState;
  readonly served: boolean;
  readonly agents_in_use: number;
  readonly max_agents: number;
  readonly pause: PauseState | null;
}
