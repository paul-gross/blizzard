import { chunkLane } from './chunk-lanes';

describe('chunkLane', () => {
  it('sits a chunk the hub flags terminal in DONE, whatever its status', () => {
    expect(chunkLane({ status: 'paused', terminal: true })).toBe('done');
  });

  it('falls back to the status’s lane when the summary omits terminal', () => {
    expect(chunkLane({ status: 'running', terminal: false })).toBe('running');
    expect(chunkLane({ status: 'stopped', terminal: true })).toBe('done');
    expect(chunkLane({ status: 'done', terminal: true })).toBe('done');
  });

  it('keeps a live status out of DONE when the hub says it is not terminal', () => {
    expect(chunkLane({ status: 'needs_human', terminal: false })).toBe('needs');
  });
});
