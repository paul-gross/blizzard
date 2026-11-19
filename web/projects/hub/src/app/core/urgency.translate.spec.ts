import type { UrgencyView } from 'fleet';
import { fromUrgencyRef, fromUrgencyView } from './urgency.translate';

const wire: UrgencyView = {
  urgency_id: 'urg-1', slug: 'now', name: 'Now', color: 'red', emoji: null,
  icon: { media_type: 'image/png', digest: 'sha256:123' }, retired: true, default: true,
  place: null, revision: 7, open_chunks: 3, created_at: '2026-01-02T00:00:00Z', created_by: 'operator',
};

describe('urgency translation', () => {
  it('carries every read fact without changing hub judgments', () => {
    expect(fromUrgencyView(wire)).toEqual({
      id: 'urg-1', slug: 'now', name: 'Now', color: 'red', emoji: null,
      icon: { mediaType: 'image/png', digest: 'sha256:123' }, retired: true, default: true,
      place: null, revision: 7, openChunks: 3, createdAt: '2026-01-02T00:00:00Z', createdBy: 'operator',
    });
    expect(fromUrgencyView({ ...wire, icon: null, emoji: '🔥', place: 0 }).icon).toBeNull();
  });
  it('translates references by identity and slug', () => {
    expect(fromUrgencyRef({ urgency_id: 'urg-1', slug: 'now' })).toEqual({ id: 'urg-1', slug: 'now' });
  });
});
