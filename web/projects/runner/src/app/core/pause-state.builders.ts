import type { PauseState } from './pause-state.domain';

/** A runner's brakes; `effective` follows the two brakes unless the caller names it. */
export function pauseState(overrides: Partial<PauseState> = {}): PauseState {
  const local = overrides.local ?? false;
  const hub = overrides.hub ?? false;
  return { local, hub, effective: local || hub, local_reason: null, ...overrides };
}
