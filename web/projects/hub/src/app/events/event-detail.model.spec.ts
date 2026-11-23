import { vi } from 'vitest';
import { eventView } from 'fleet/testing';
import { eventLogEntry } from './event-log.builders';
import { fromEventView } from './event-log.translate';
import { eventFingerprint, findEvent } from './event-detail.model';

describe('projected event identity', () => {
  const event = eventLogEntry({
    id: -1, recordedAt: '2026-07-16T00:00:00Z', kind: 'needs-human', chunkId: 'ch_1',
    message: 'original', detail: { z: 1, nested: { b: 2, a: 1 } },
  });
  beforeEach(() => vi.stubGlobal('crypto', { subtle: {
    digest: async (_algorithm: string, data: Uint8Array) => {
      const result = new Uint8Array(32);
      for (let i = 0; i < data.length; i++) result[i % 32] = (result[i % 32] * 31 + data[i]) & 255;
      return result.buffer;
    },
  } }));
  afterEach(() => vi.unstubAllGlobals());
  const find = (events: readonly typeof event[], fingerprint: string | null, id = '-1') =>
    findEvent(events, id, event.recordedAt, event.kind, event.chunkId, fingerprint);

  it('resolves a shifted id, renamed runner, and reordered detail keys', async () => {
    const moved = { ...event, id: -9, runnerName: 'new name', detail: { nested: { a: 1, b: 2 }, z: 1 } };
    expect(await find([moved], await eventFingerprint(event))).toEqual(moved);
  });
  it('refuses a replacement with the same tuple but a different message or detail', async () => {
    const fingerprint = await eventFingerprint(event);
    expect(await find([{ ...event, id: -2, message: 'replacement' }], fingerprint)).toBeNull();
    expect(await find([{ ...event, id: -2, detail: { z: 2 } }], fingerprint)).toBeNull();
  });
  it('refuses missing, ambiguous, or unverifiable fingerprints', async () => {
    const fingerprint = await eventFingerprint(event);
    expect(await find([], fingerprint)).toBeNull();
    expect(await find([event, { ...event, id: -2 }], fingerprint)).toBeNull();
    expect(await find([event], null)).toBeNull();
    vi.stubGlobal('crypto', {});
    expect(await find([event], fingerprint)).toBeNull();
  });
  it('requires the id and tuple for persisted events', async () => {
    const persisted = { ...event, id: 4 };
    expect(await find([persisted], null, '4')).toEqual(persisted);
    expect(await find([persisted], null, '5')).toBeNull();
    expect(await find([persisted], null, '-1')).toBeNull();
  });
});

describe('projected event fingerprint', () => {
  // The SHA-256 a projected event's link carries; a link minted before the domain move must still resolve.
  const PINNED = '9ece887afaad6c5bb79f94859aadc01096c58f6b6eeeadb8e923ed1695c384b3';
  const wire = eventView({
    id: -1, recorded_at: '2026-07-16T00:00:00Z', kind: 'needs-human', chunk_id: 'ch_1', severity: 'critical',
    runner_id: null, runner_name: 'ignored', lease_id: 'ls_1', node_name: 'review', message: 'needs a human',
    detail: { z: 1, nested: { b: 2, a: 1 } },
  });

  it('hashes a served projected event to its pinned fingerprint', async () => {
    expect(await eventFingerprint(fromEventView(wire))).toBe(PINNED);
  });

  it('hashes an unforeseen severity by its raw text', async () => {
    expect(await eventFingerprint(fromEventView({ ...wire, severity: 'emergency' as never })))
      .toBe('ee871ec566b2f8c83067ef812db00d6b2871335a43aac3ffbfbd3e1357005f3e');
  });
});
