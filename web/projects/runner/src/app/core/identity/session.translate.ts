import type { runnerApi } from 'fleet/runner-shell';

import type { RunnerSession } from './session.domain';

export function fromRunnerAuthSessionView(wire: runnerApi.RunnerAuthSessionView): RunnerSession {
  return { auth_enabled: wire.auth_enabled, username: wire.username ?? null };
}
