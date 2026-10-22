import type { ChunkStatus } from '../api/hub';
import { STATUS_TONE, UNKNOWN_LANE, chunkLane, unknownLane } from './chunk-lanes';

/** A status a newer hub may send that this build's generated vocabulary does not list. */
const NEWER_STATUS = 'newer-status' as ChunkStatus;

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

  it('sits a chunk with a status this build does not know in the unknown lane', () => {
    expect(chunkLane({ status: NEWER_STATUS, terminal: false })).toBe(UNKNOWN_LANE);
    expect(chunkLane({ status: NEWER_STATUS, terminal: true })).toBe('done');
  });
});

describe('unknownLane', () => {
  it('is absent while every chunk carries a status this build knows', () => {
    expect(unknownLane([{ status: 'running', terminal: false }, { status: 'ready', terminal: false }])).toBeNull();
  });

  it('heads the lane with each raw status a chunk carries, once each', () => {
    const lane = unknownLane([{ status: NEWER_STATUS, terminal: false }, { status: 'running', terminal: false }, { status: NEWER_STATUS, terminal: false }, { status: 'other-newer-status' as ChunkStatus, terminal: false }]);
    expect(lane).toEqual({ key: UNKNOWN_LANE, label: 'newer-status · other-newer-status', headerLabel: 'newer-status · other-newer-status' });
  });

  it('leaves out a chunk the hub flags terminal, which DONE holds', () => {
    expect(unknownLane([{ status: NEWER_STATUS, terminal: true }])).toBeNull();
  });
});

describe('STATUS_TONE', () => {
  it('reads a status this build does not know as idle', () => {
    expect(STATUS_TONE(NEWER_STATUS)).toBe('idle');
  });
});
