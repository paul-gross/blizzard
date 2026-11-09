import type { hubApi } from 'fleet';

export interface BoardDonePolicy {
  readonly heading: string;
  readonly explanation: string;
  readonly cutoffs: readonly string[];
}

const SERVER_SELECTED: BoardDonePolicy = {
  heading: 'Done',
  explanation: 'Server-selected completed work',
  cutoffs: [],
};

export function boardDonePolicy(pages: readonly hubApi.ChunksPageView[]): BoardDonePolicy {
  if (pages.length === 0) return SERVER_SELECTED;
  const policies = pages.map((page) => page.board_done_policy);
  if (policies.every((policy) => policy === null)) {
    return { heading: 'Done', explanation: 'All completed work in this scope', cutoffs: [] };
  }
  const first = policies[0];
  if (!first || !Number.isSafeInteger(first.window_seconds) || first.window_seconds <= 0 ||
      !Number.isSafeInteger(first.floor) || first.floor < 0) return SERVER_SELECTED;
  if (policies.some((policy) => !policy || policy.window_seconds !== first.window_seconds ||
      policy.floor !== first.floor || typeof policy.done_since !== 'string' ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(policy.done_since) ||
      !Number.isFinite(Date.parse(policy.done_since)))) return SERVER_SELECTED;

  const duration = first.window_seconds % 3600 === 0
    ? `${first.window_seconds / 3600} hours`
    : `${first.window_seconds} seconds`;
  return {
    heading: 'Done',
    explanation: `At each page read: completed within ${duration} of that read or among the newest ${first.floor}. Page cutoffs: ${policies.map((policy) => policy!.done_since).join(', ')}`,
    cutoffs: policies.map((policy) => policy!.done_since),
  };
}
