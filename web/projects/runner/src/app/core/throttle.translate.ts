import type { runnerApi } from 'fleet/runner-shell';

import type { Throttle } from './throttle.domain';

/** A runner's throttle, or `null` when the read reports none. */
export function fromThrottleStatusView(wire: runnerApi.ThrottleStatusView | null | undefined): Throttle | null {
  if (!wire) return null;
  const { floor } = wire;
  return {
    state: wire.state,
    declining: [...wire.declining],
    floor: {
      admits: floor.admits,
      subscription: floor.subscription,
      hold: floor.hold && {
        kind: floor.hold.kind,
        harness_id: floor.hold.harness_id,
        lease_id: floor.hold.lease_id,
        window: floor.hold.window,
        expected_reset: floor.hold.expected_reset,
        awaits_sample: floor.hold.awaits_sample,
        grace_opens_at: floor.hold.grace_opens_at,
      },
      clear: floor.clear && { by: floor.clear.by, cleared_at: floor.clear.cleared_at },
    },
    levers: wire.levers.map(({ name, setting, reading, until, admits }) => ({ name, setting, reading, until, admits })),
    on_decline: wire.on_decline,
    on_usage_limit: wire.on_usage_limit,
  };
}
