import { describe, expect, it } from 'vitest';
import { runnerAuthSessionView } from 'fleet/testing';

import { fromRunnerAuthSessionView } from './session.translate';

describe('fromRunnerAuthSessionView', () => {
  it('carries a signed-in session', () => {
    expect(fromRunnerAuthSessionView(runnerAuthSessionView({ username: 'alice' }))).toEqual({ auth_enabled: true, username: 'alice' });
  });

  it('carries a gated session with no username', () => {
    expect(fromRunnerAuthSessionView(runnerAuthSessionView({ username: null }))).toEqual({ auth_enabled: true, username: null });
  });

  it('carries an authless surface', () => {
    expect(fromRunnerAuthSessionView(runnerAuthSessionView({ auth_enabled: false, username: null }))).toEqual({
      auth_enabled: false,
      username: null,
    });
  });
});
