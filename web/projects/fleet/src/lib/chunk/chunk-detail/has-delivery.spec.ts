import { describe, expect, it } from 'vitest';

import type { ChunkPending } from '../chunk-page/chunk-detail.model';
import { hasDelivery } from './has-delivery';

describe('hasDelivery', () => {
  const empty = { open_prs: [], closed_prs: [], landed_repos: [], awaiting_external_merge: false, pending: null };
  const pr = { repo: 'r', number: 1, url: 'https://example.com/pr/1' };
  const pending = (kind: ChunkPending['kind']) => ({
    node_name: 'park',
    next_poll_at: '2026-10-30T12:05:00Z',
    kind,
    since: null,
  });

  it('holds for a PR open or closed, a landed repo, or an operator merge wait', () => {
    expect(hasDelivery(empty)).toBe(false);
    expect(hasDelivery({ ...empty, open_prs: [pr] })).toBe(true);
    expect(hasDelivery({ ...empty, closed_prs: [pr] })).toBe(true);
    expect(hasDelivery({ ...empty, landed_repos: [{ repo: 'r', commit_hash: 'abc', url: null }] })).toBe(true);
    expect(hasDelivery({ ...empty, awaiting_external_merge: true })).toBe(true);
  });

  it('holds for a parked wait with nothing opened yet, and not for another pending kind', () => {
    expect(hasDelivery({ ...empty, pending: pending({ kind: 'awaiting-merge' }) })).toBe(true);
    expect(hasDelivery({ ...empty, pending: pending({ kind: 'pending' }) })).toBe(false);
    expect(hasDelivery({ ...empty, pending: pending({ kind: 'unknown', raw: 'future-kind' }) })).toBe(false);
  });
});
