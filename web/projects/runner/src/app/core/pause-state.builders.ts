import type { PauseState } from './pause-state.domain';

export function pauseState(overrides: Partial<PauseState> = {}): PauseState {
  return { local: false, hub: false, local_reason: null, ...overrides };
}
