import { describe, expect, it } from 'vitest';

import { bounceReason, parseBounceEnvelope } from './delivery-bounce.model';

describe('the bounce envelope', () => {
  it('reads its detail and failed step', () => {
    expect(parseBounceEnvelope(JSON.stringify({ detail: 'boom', step: { name: 'test', exit_code: 2 } }))).toEqual({ detail: 'boom', step: { name: 'test', exitCode: 2 } });
  });

  it('reads an empty detail, a nameless step, or a non-numeric exit as absent', () => {
    expect(parseBounceEnvelope(JSON.stringify({ detail: '', step: { exit_code: 1 } }))).toEqual({ detail: null, step: null });
    expect(parseBounceEnvelope(JSON.stringify({ step: { name: 'test', exit_code: 'x' } })).step).toEqual({ name: 'test', exitCode: null });
  });

  it('reads an envelope that is not JSON, or is null, as carrying nothing', () => {
    expect(parseBounceEnvelope('not json')).toEqual({ detail: null, step: null });
    expect(parseBounceEnvelope('null')).toEqual({ detail: null, step: null });
  });

  it('gives the reason as the detail, else the step\'s exit, else nothing', () => {
    expect(bounceReason({ detail: 'boom', step: { name: 'test', exitCode: 2 } })).toBe('boom');
    expect(bounceReason({ detail: null, step: { name: 'test', exitCode: 2 } })).toBe('test exited 2');
    expect(bounceReason({ detail: null, step: { name: 'test', exitCode: null } })).toBe('test failed');
    expect(bounceReason({ detail: null, step: null })).toBeNull();
  });
});
