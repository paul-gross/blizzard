import type { BoardDoneWindow, BoardPageMetadata } from './chunks.domain';
import { boardDonePolicy } from './board-done-policy.model';

function page(policy: BoardDoneWindow | null): BoardPageMetadata {
  return { next_cursor: null, board_done_policy: policy };
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
  it('describes nondefault server values rather than assuming the default window and floor', () => {
    const custom = { window_seconds: 7200, floor: 3, done_since: '2026-11-01T10:00:00Z' };
    expect(boardDonePolicy([page(custom)])).toEqual({
      heading: 'Done',
      explanation: `At each page read: completed within 2 hours of that read or among the newest 3. Page cutoffs: ${custom.done_since}`,
      cutoffs: [custom.done_since],
    });
  });
  it('falls back for absent, mixed, malformed or conflicting policy', () => {
    const missing = { next_cursor: null, board_done_policy: undefined };
    for (const pages of [[], [missing], [page(null), page(policy)],
      [page({ ...policy, done_since: 'invalid' })], [page({ ...policy, done_since: '0' })], [page({ ...policy, window_seconds: -1 })],
      [page({ ...policy, floor: 1.5 })], [page(policy), page({ ...policy, floor: 11 })]]) {
      expect(boardDonePolicy(pages)).toEqual({ heading: 'Done', explanation: 'Server-selected completed work', cutoffs: [] });
    }
  });
});
