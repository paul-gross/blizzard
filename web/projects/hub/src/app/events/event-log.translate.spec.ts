import { eventView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { eventSeverity, fromEventView, narrowEventSeverity } from './event-log.translate';

describe('eventSeverity', () => {
  it('reads each known severity as itself', () => {
    expect(['info', 'warning', 'critical'].map(eventSeverity)).toEqual(['info', 'warning', 'critical']);
  });

  it('keeps an unforeseen severity raw', () => {
    expect(eventSeverity('emergency')).toEqual({ kind: 'unknown', raw: 'emergency' });
  });
});

describe('narrowEventSeverity', () => {
  it('passes a listed severity and refuses anything else', () => {
    expect(narrowEventSeverity('warning')).toBe('warning');
    expect(narrowEventSeverity('')).toBeNull();
    expect(narrowEventSeverity('emergency')).toBeNull();
  });
});

describe('fromEventView', () => {
  it('carries every field as served', () => {
    const wire = eventView({
      id: -3, recorded_at: '2026-07-16T00:00:00Z', severity: 'critical', kind: 'needs-human', message: 'stuck',
      runner_id: 'rn_1', runner_name: 'alpha', chunk_id: 'ch_1', lease_id: 'ls_1', node_name: 'review',
      detail: { reason: 'timeout' },
    });
    expect(fromEventView(wire)).toEqual({
      id: -3, recordedAt: '2026-07-16T00:00:00Z', severity: 'critical', kind: 'needs-human', message: 'stuck',
      runnerId: 'rn_1', runnerName: 'alpha', chunkId: 'ch_1', leaseId: 'ls_1', nodeName: 'review',
      detail: { reason: 'timeout' },
    });
  });

  it('keeps the nullable fields null', () => {
    const entry = fromEventView(eventView({ runner_id: null, chunk_id: null, detail: null }));
    expect([entry.runnerId, entry.runnerName, entry.chunkId, entry.leaseId, entry.nodeName, entry.detail])
      .toEqual([null, null, null, null, null, null]);
  });

  it('keeps an unforeseen severity raw', () => {
    expect(fromEventView(eventView({ severity: 'emergency' as never })).severity)
      .toEqual({ kind: 'unknown', raw: 'emergency' });
  });
});
