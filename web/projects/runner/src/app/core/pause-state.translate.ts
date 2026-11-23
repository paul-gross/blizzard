import type { runnerApi } from 'fleet/runner-shell';

import type { PauseState } from './pause-state.domain';

/** A runner's brakes, or `null` when the host reports none. */
export function fromPauseStateView(wire: runnerApi.PauseStateView | null | undefined): PauseState | null {
  if (!wire) return null;
  return { local: wire.local, hub: wire.hub, local_reason: wire.local_reason };
}
