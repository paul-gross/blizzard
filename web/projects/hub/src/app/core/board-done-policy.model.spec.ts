import type { hubApi } from 'fleet';
import { boardDonePolicy } from './board-done-policy.model';

function page(policy: hubApi.ChunksPageView['board_done_policy']): hubApi.ChunksPageView {
  return { chunks: [], next_cursor: null, board_done_policy: policy };
}
const policy = { window_seconds: 172800, floor: 10, done_since: '2026-11-01T00:00:00Z' };

describe('boardDonePolicy', () => {
  it('describes a consistent window and floor, retaining each page cutoff', () => {
    const next = { ...policy, done_since: '2026-11-02T00:00:00Z' };
    expect(boardDonePolicy([page(policy), page(next)])).toEqual({
      heading: 'Done', explanation: `At each page read: completed within 48 hours of that read or among the newest 10. Page cutoffs: ${policy.done_since}, ${next.done_since}`,
      cutoffs: [policy.done_since, next.done_since],
    });
  });
  it('recognizes uniform explicit null', () => {
    expect(boardDonePolicy([page(null), page(null)]).explanation).toBe('All completed work in this scope');
  });
  it('falls back for absent, mixed, malformed or conflicting policy', () => {
    const missing = { chunks: [], next_cursor: null } as unknown as hubApi.ChunksPageView;
    for (const pages of [[], [missing], [page(null), page(policy)],
      [page({ ...policy, done_since: 'invalid' })], [page({ ...policy, done_since: '0' })], [page({ ...policy, window_seconds: -1 })],
      [page({ ...policy, floor: 1.5 })], [page(policy), page({ ...policy, floor: 11 })]]) {
      expect(boardDonePolicy(pages)).toEqual({ heading: 'Done', explanation: 'Server-selected completed work', cutoffs: [] });
    }
  });
});
