import { describe, expect, it } from 'vitest';
import { runnerAuthSessionView } from 'fleet/testing';

import { fromRunnerAuthSessionView } from './session.translate';

describe('fromRunnerAuthSessionView', () => {
  it('carries a signed-in session', () => {
    expect(
      fromRunnerAuthSessionView(runnerAuthSessionView({ username: 'alice' })),
    ).toEqual({
      auth_enabled: true,
      username: 'alice',
      superuser: false,
    });
  });

  it('carries a gated session with no username', () => {
    expect(
      fromRunnerAuthSessionView(runnerAuthSessionView({ username: null })),
    ).toEqual({
      auth_enabled: true,
      username: null,
      superuser: false,
    });
  });

  it('carries an authless surface', () => {
    expect(
      fromRunnerAuthSessionView(
        runnerAuthSessionView({
          auth_enabled: false,
          username: null,
          superuser: true,
        }),
      ),
    ).toEqual({
      auth_enabled: false,
      username: null,
      superuser: true,
    });
  });

  it("carries the runner's superuser judgment", () => {
    expect(
      fromRunnerAuthSessionView(runnerAuthSessionView({ superuser: true }))
        .superuser,
    ).toBe(true);
  });
});
