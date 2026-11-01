import { describe, expect, it } from 'vitest';

import { chunkDetail, prView } from '../../testing/builders/chunk';
import { hasDelivery } from './has-delivery';

describe('hasDelivery', () => {
  const empty = chunkDetail({ open_prs: [], closed_prs: [], landed_repos: [], awaiting_external_merge: false, pending: null });
  const pending = (kind: string) => ({ node_name: 'park', next_poll_at: '2026-10-30T12:05:00Z', kind, since: null });

  it('holds for a PR open or closed, a landed repo, or an operator merge wait', () => {
    expect(hasDelivery(empty)).toBe(false);
    expect(hasDelivery({ ...empty, open_prs: [prView()] })).toBe(true);
    expect(hasDelivery({ ...empty, closed_prs: [prView()] })).toBe(true);
    expect(hasDelivery({ ...empty, landed_repos: [{ repo: 'r', commit_hash: 'abc', url: null }] })).toBe(true);
    expect(hasDelivery({ ...empty, awaiting_external_merge: true })).toBe(true);
  });

  it('holds for a parked wait with nothing opened yet, and not for another pending kind', () => {
    expect(hasDelivery({ ...empty, pending: pending('awaiting-merge') })).toBe(true);
    expect(hasDelivery({ ...empty, pending: pending('future-kind') })).toBe(false);
  });
});
