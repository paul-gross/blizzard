import { describe, expect, it } from 'vitest';

import { runnerSession } from './session.builders';
import { lacksSession, resolvesUser, signedInUsername } from './session.domain';

describe('signedInUsername', () => {
  it('reads the username of a signed-in session', () => {
    expect(signedInUsername(runnerSession({ username: 'alice' }))).toBe('alice');
  });

  it('reads null while signed out — auth on, no username', () => {
    expect(signedInUsername(runnerSession({ username: null }))).toBeNull();
  });

  it('reads null with auth off, whatever the username', () => {
    expect(signedInUsername(runnerSession({ auth_enabled: false, username: 'alice' }))).toBeNull();
  });

  it('reads null before the session resolves', () => {
    expect(signedInUsername(undefined)).toBeNull();
  });
});

describe('resolvesUser and lacksSession', () => {
  it('a signed-in session resolves a user and lacks nothing', () => {
    expect(resolvesUser(runnerSession())).toBe(true);
    expect(lacksSession(runnerSession())).toBe(false);
  });

  it('a signed-out gated session lacks its session', () => {
    expect(resolvesUser(runnerSession({ username: null }))).toBe(false);
    expect(lacksSession(runnerSession({ username: null }))).toBe(true);
  });

  it('an authless surface neither resolves a user nor lacks a session', () => {
    expect(resolvesUser(runnerSession({ auth_enabled: false, username: null }))).toBe(false);
    expect(lacksSession(runnerSession({ auth_enabled: false, username: null }))).toBe(false);
  });
});
