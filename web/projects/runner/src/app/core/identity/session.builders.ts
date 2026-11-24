import type { RunnerSession } from './session.domain';

export function runnerSession(
  overrides: Partial<RunnerSession> = {},
): RunnerSession {
  return {
    auth_enabled: true,
    username: 'alice',
    superuser: false,
    ...overrides,
  };
}
