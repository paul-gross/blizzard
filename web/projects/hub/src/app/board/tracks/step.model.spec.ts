import { describe, expect, it } from 'vitest';

import {
  type EnvTimelineSegment,
  type PlacedStep,
  costLabel,
  hostLabel,
  nodeShade,
  segmentCost,
  stepEndMs,
  stepHeld,
  stepHeldMs,
  refNames,
  stepRefKey,
  stepRefOf,
  stepRefOfParams,
  stepRefOfSegment,
  stepRefParams,
} from './step.model';

const seg = (overrides: Partial<EnvTimelineSegment> = {}): EnvTimelineSegment => ({
  chunk_id: 'ch_AAAA0001',
  cost_estimated: false,
  cost_usd: 0,
  ended_at: '2026-07-01T10:10:00Z',
  epoch: 1,
  escalation: null,
  escalation_open: false,
  executor: 'runner',
  exit_choice: null,
  failed: false,
  graph_id: null,
  graph_name: 'delivery',
  lease_id: 'lease_L1',
  node_id: 'nd_build',
  node_name: 'build',
  project_id: null,
  runner_id: 'rn_1',
  runner_name: 'r1',
  started_at: '2026-07-01T10:00:00Z',
  title: null,
  ...overrides,
});

describe('step refs', () => {
  it('names a step by the session that ran it', () => {
    expect(stepRefOfSegment(seg())).toEqual({ chunkId: 'ch_AAAA0001', session: 'lease_L1', at: null });
  });

  it('names a step no session ran by the instant it began', () => {
    expect(stepRefOfSegment(seg({ lease_id: null }))).toEqual({ chunkId: 'ch_AAAA0001', session: null, at: '2026-07-01T10:00:00Z' });
  });

  it('refs a placed step by its segment', () => {
    const placed: PlacedStep = { environment_id: 'e1', host_id: null, host_name: null, segment: seg(), workspace_id: 'ws1' };
    expect(stepRefOf(placed)).toEqual({ chunkId: 'ch_AAAA0001', session: 'lease_L1', at: null });
  });

  it('keys a ref by whichever name it carries', () => {
    expect(stepRefKey({ chunkId: 'c', session: 'lease_1', at: null })).toBe('lease_1');
    expect(stepRefKey({ chunkId: 'c', session: null, at: '2026-07-01T10:00:00Z' })).toBe('2026-07-01T10:00:00Z');
  });

  it('reads ?step as a session', () => {
    expect(stepRefOfParams(new URLSearchParams('chunk=ch_1&step=lease_9'))).toEqual({ chunkId: 'ch_1', session: 'lease_9', at: null });
  });

  it('reads ?at as an instant', () => {
    expect(stepRefOfParams(new URLSearchParams('chunk=ch_1&at=2026-07-01T10:00:00Z'))).toEqual({
      chunkId: 'ch_1',
      session: null,
      at: '2026-07-01T10:00:00Z',
    });
  });

  it('never reads both, the session winning', () => {
    expect(stepRefOfParams(new URLSearchParams('chunk=ch_1&step=lease_9&at=2026-07-01T10:00:00Z'))).toEqual({
      chunkId: 'ch_1',
      session: 'lease_9',
      at: null,
    });
  });

  it('reads a chunk alone as its newest step', () => {
    expect(stepRefOfParams(new URLSearchParams('chunk=ch_1'))).toEqual({ chunkId: 'ch_1', session: null, at: null });
  });

  it('reads no selection without a chunk', () => {
    expect(stepRefOfParams(new URLSearchParams('step=lease_9'))).toBeNull();
  });

  it.each([
    { chunkId: 'ch_1', session: 'lease_9', at: null },
    { chunkId: 'ch_1', session: null, at: '2026-07-01T10:00:00Z' },
    { chunkId: 'ch_1', session: null, at: null },
  ])('writes params that read back as the same ref: %o', (ref) => {
    const search = new URLSearchParams();
    for (const [k, v] of Object.entries(stepRefParams(ref))) if (v !== null) search.set(k, v);
    expect(stepRefOfParams(search)).toEqual(ref);
  });

  it('clears all three params for no ref', () => {
    expect(stepRefParams(null)).toEqual({ chunk: null, step: null, at: null });
  });

  it('tells whether a ref names a step itself', () => {
    expect(refNames({ chunkId: 'ch_AAAA0001', session: 'lease_L1', at: null }, seg())).toBe(true);
    expect(refNames({ chunkId: 'ch_AAAA0001', session: 'lease_L2', at: null }, seg())).toBe(false);
    expect(refNames({ chunkId: 'ch_OTHER', session: 'lease_L1', at: null }, seg())).toBe(false);
    expect(refNames({ chunkId: 'ch_AAAA0001', session: null, at: '2026-07-01T10:00:00Z' }, seg({ lease_id: null }))).toBe(true);
    expect(refNames({ chunkId: 'ch_AAAA0001', session: null, at: '2026-07-01T10:00:00Z' }, seg())).toBe(false);
    expect(refNames({ chunkId: 'ch_AAAA0001', session: null, at: null }, seg())).toBe(false);
  });
});

describe('nodeShade', () => {
  it('deepens along the delivery graphs usual node order', () => {
    expect(['plan', 'plan-review', 'build', 'verify', 'review', 'resolve', 'pre-push', 'deliver'].map(nodeShade)).toEqual([
      0, 1, 2, 3, 4, 5, 6, 7,
    ]);
  });

  it('hashes an unlisted node to a stable shade on the ramp', () => {
    const shade = nodeShade('custom-node');
    expect(shade).toBe(nodeShade('custom-node'));
    expect(Number.isInteger(shade)).toBe(true);
    expect(shade).toBeGreaterThanOrEqual(0);
    expect(shade).toBeLessThanOrEqual(7);
  });
});

describe('step timing', () => {
  const now = Date.parse('2026-07-01T10:30:00Z');

  it('ends a finished step at its end', () => {
    expect(stepEndMs(seg(), now)).toBe(Date.parse('2026-07-01T10:10:00Z'));
  });

  it('ends a running step now', () => {
    expect(stepEndMs(seg({ ended_at: null }), now)).toBe(now);
  });

  it('holds a finished step from start to end', () => {
    expect(stepHeldMs(seg(), now)).toBe(10 * 60_000);
    expect(stepHeld(seg(), now)).toBe('10m');
  });

  it('holds a running step from start to now', () => {
    expect(stepHeldMs(seg({ ended_at: null }), now)).toBe(30 * 60_000);
    expect(stepHeld(seg({ ended_at: null }), now)).toBe('30m');
  });
});

describe('costLabel', () => {
  it('says no usage yet for a running step with none recorded', () => {
    expect(costLabel(0, false, true)).toBe('no usage yet');
  });

  it('writes zero for a finished step with none', () => {
    expect(costLabel(0, false, false)).toBe('$0.00');
  });

  it('writes a billed figure plain and an estimate with a tilde', () => {
    expect(costLabel(1.5, false, false)).toBe('$1.50');
    expect(costLabel(1.5, true, false)).toBe('~$1.50');
  });

  it('writes a running figure once usage has landed', () => {
    expect(costLabel(0.25, false, true)).toBe('$0.25');
  });

  it('labels a segment by its own spend and whether it still runs', () => {
    expect(segmentCost(seg({ cost_usd: 2, cost_estimated: true }))).toBe('~$2.00');
    expect(segmentCost(seg({ ended_at: null }))).toBe('no usage yet');
  });
});

describe('hostLabel', () => {
  it('names a host without an id as no host', () => {
    expect(hostLabel({ host_id: null, host_name: null })).toBe('no host');
  });

  it('prefers the hosts name', () => {
    expect(hostLabel({ host_id: 'hs_ABCDWXYZ', host_name: 'xps' })).toBe('xps');
  });

  it('falls back to the hosts compact id', () => {
    expect(hostLabel({ host_id: 'hs_ABCDWXYZ', host_name: null })).toBe('H-WXYZ');
  });
});
