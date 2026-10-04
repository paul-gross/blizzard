import { describe, expect, it } from 'vitest';

import { authState, connectionLabel } from './app.model';

describe('authState', () => {
  it('is loading while the identity read is pending', () => {
    expect(authState(true, null)).toBe('loading');
    expect(authState(true, { permissions: ['fleet:view'] })).toBe('loading');
  });

  it('is unauthenticated without an identity', () => {
    expect(authState(false, null)).toBe('unauthenticated');
  });

  it('is lobby for an identity holding no permission', () => {
    expect(authState(false, { permissions: [] })).toBe('lobby');
  });

  it('is ready for an identity holding a permission', () => {
    expect(authState(false, { permissions: ['fleet:view'] })).toBe('ready');
  });
});

describe('connectionLabel', () => {
  it('reports a reconnecting stream ahead of the health read', () => {
    expect(connectionLabel('reconnecting', true, true, 'ok')).toBe('reconnecting…');
  });

  it('is connecting while the health read is pending', () => {
    expect(connectionLabel('idle', true, false, undefined)).toBe('connecting…');
  });

  it('is offline once the health read fails', () => {
    expect(connectionLabel('closed', false, true, undefined)).toBe('offline');
  });

  it("reports the health read's own status, defaulting to ok", () => {
    expect(connectionLabel('open', false, false, 'degraded')).toBe('degraded');
    expect(connectionLabel('open', false, false, undefined)).toBe('ok');
  });
});
