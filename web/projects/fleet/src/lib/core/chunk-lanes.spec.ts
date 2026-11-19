import { LANES, UNKNOWN_LANE, laneTone, STATUS_LANE, STATUS_TONE, chunkLane, unknownLane } from './chunk-lanes';

/** A status a newer hub may send that this build's generated vocabulary does not list. */
const NEWER_STATUS = 'newer-status';

describe('chunkLane', () => {
  it('sits a chunk the hub flags terminal in DONE, whatever its status', () => {
    expect(chunkLane({ status: { kind: 'paused' }, terminal: true })).toBe('done');
  });

  it('falls back to the status’s lane when the summary omits terminal', () => {
    expect(chunkLane({ status: { kind: 'running' }, terminal: false })).toBe('running');
    expect(chunkLane({ status: { kind: 'stopped' }, terminal: true })).toBe('done');
    expect(chunkLane({ status: { kind: 'done' }, terminal: true })).toBe('done');
    expect(chunkLane({ status: { kind: 'stopped' } })).toBe('done');
    expect(chunkLane({ status: { kind: 'done' } })).toBe('done');
  });

  it('keeps a live status out of DONE when the hub says it is not terminal', () => {
    expect(chunkLane({ status: { kind: 'needs_human' }, terminal: false })).toBe('needs');
  });

  it('sits a chunk with a status this build does not know in the unknown lane', () => {
    expect(chunkLane({ status: { kind: 'unknown', raw: NEWER_STATUS }, terminal: false })).toBe(UNKNOWN_LANE);
    expect(chunkLane({ status: { kind: 'unknown', raw: NEWER_STATUS }, terminal: true })).toBe('done');
  });
});

describe('unknownLane', () => {
  it('is absent while every chunk carries a status this build knows', () => {
    expect(unknownLane([{ status: { kind: 'running' }, terminal: false }, { status: { kind: 'ready' }, terminal: false }])).toBeNull();
  });

  it('heads the lane with each raw status a chunk carries, once each', () => {
    const lane = unknownLane([{ status: { kind: 'unknown', raw: NEWER_STATUS }, terminal: false }, { status: { kind: 'running' }, terminal: false }, { status: { kind: 'unknown', raw: NEWER_STATUS }, terminal: false }, { status: { kind: 'unknown', raw: 'other-newer-status' }, terminal: false }]);
    expect(lane).toEqual({ key: UNKNOWN_LANE, label: 'newer-status · other-newer-status', headerLabel: 'newer-status · other-newer-status' });
  });

  it('leaves out a chunk the hub flags terminal, which DONE holds', () => {
    expect(unknownLane([{ status: { kind: 'unknown', raw: NEWER_STATUS }, terminal: true }])).toBeNull();
  });
});

describe('STATUS_TONE', () => {
  it('reads a status this build does not know as idle', () => {
    expect(STATUS_TONE(NEWER_STATUS)).toBe('idle');
  });
});

describe('laneTone', () => {
  it('gives every board lane its tone', () => {
    expect(Object.fromEntries(LANES.map((lane) => [lane.key, laneTone(lane.key)]))).toEqual({
      notready: 'idle',
      ready: 'idle',
      waiting: 'waiting',
      needs: 'needs',
      running: 'running',
      done: 'done',
    });
  });

  it('agrees with STATUS_TONE for every status', () => {
    for (const status of STATUS_LANE.keys) expect(laneTone(STATUS_LANE(status))).toBe(STATUS_TONE(status));
  });

  it('reads idle for the unknown lane and any other key', () => {
    expect(laneTone(UNKNOWN_LANE)).toBe('idle');
    expect(laneTone('toString')).toBe('idle');
  });
});
