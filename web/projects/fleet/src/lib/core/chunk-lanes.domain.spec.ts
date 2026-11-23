import { chunkLane, laneCounts, STATUS_TONE, unknownLane } from './chunk-lanes';

describe('lane domain', () => {
  it('gives terminal precedence and retains the legacy terminal-status fallback', () => {
    expect(chunkLane({ status: { kind: 'paused' }, terminal: true })).toBe('done');
    expect(chunkLane({ status: { kind: 'stopped' } })).toBe('done');
    expect(chunkLane({ status: { kind: 'done' } })).toBe('done');
  });

  it('seats unknown statuses by their raw spelling without inventing a count', () => {
    const status = { kind: 'unknown', raw: 'New-Status' } as const;
    expect(chunkLane({ status })).toBe('unknown');
    expect(unknownLane([{ status }])?.label).toBe('New-Status');
    expect(STATUS_TONE(status.kind)).toBe('idle');
    expect(laneCounts({ statuses: { not_ready: 0, ready: 0, running: 0, delivering: 0, waiting_on_human: 0, needs_human: 0, paused: 0, stopped: 0, done: 0 }, terminal: 0, total: 1 }).has('unknown')).toBe(false);
  });
});
