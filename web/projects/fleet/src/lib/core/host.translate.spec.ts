import { hostConnection, renewalFailure, renewalResult, sampleMiss } from './host.translate';

describe('host vocabulary translation', () => {
  it('recognizes every known case', () => {
    for (const value of ['credential_lapsed', 'credential_unreadable', 'endpoint_unreachable', 'response_unparseable']) {
      expect(sampleMiss(value)).toBe(value);
    }
    for (const value of ['renewed', 'failed', 'unrecorded']) expect(renewalResult(value)).toBe(value);
    for (const value of ['renewer_unavailable', 'timed_out', 'vendor_refused', 'protocol_error']) {
      expect(renewalFailure(value)).toBe(value);
    }
    for (const value of ['never_connected', 'online', 'offline']) expect(hostConnection(value)).toBe(value);
  });

  it('preserves unknown spelling and nulls', () => {
    const unknown = { kind: 'unknown', raw: 'Future-Value' };
    expect(sampleMiss('Future-Value')).toEqual(unknown);
    expect(renewalResult('Future-Value')).toEqual(unknown);
    expect(renewalFailure('Future-Value')).toEqual(unknown);
    expect(hostConnection('Future-Value')).toEqual(unknown);
    expect(sampleMiss(null)).toBeNull();
    expect(renewalResult(undefined)).toBeNull();
    expect(renewalFailure(null)).toBeNull();
  });
});
