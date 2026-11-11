import type { hubApi } from 'fleet';

/** Presentation of the hub's bounded walk, without making a claimability judgment. */
export function nextClaimSummary(next: hubApi.NextClaimResponse) {
  return {
    next: next.next,
    passed: next.passed,
    omitted: Math.max(0, next.passed_total - next.passed.length),
    unserved: next.unserved,
    ifHolding: next.if_holding,
    heldAt: next.held_at,
  };
}

export function prerequisiteIds(next: hubApi.NextClaimResponse | undefined): string[] {
  return [...new Set([...(next?.passed ?? []), ...(next?.held_at ?? [])]
    .map((entry) => entry.prerequisite).filter((id): id is string => id !== null))];
}

export function prerequisiteStatuses(results: readonly { data?: hubApi.ChunkDetail; isError?: boolean }[]): Readonly<Record<string, string>> {
  return Object.fromEntries(results.filter((result) => result.data && !result.isError).map((result) => [result.data!.chunk_id, result.data!.status]));
}

export function claimUrgencyIcons(next: hubApi.NextClaimResponse | undefined, urgencies: readonly hubApi.UrgencyView[] | undefined): { key: string; digest: string }[] {
  const ids = new Set([next?.next, next?.if_holding, ...(next?.passed ?? []), ...(next?.held_at ?? [])].map((entry) => entry?.urgency?.urgency_id));
  return (urgencies ?? []).filter((item) => ids.has(item.urgency_id) && !item.default && !item.emoji && item.icon)
    .map((item) => ({ key: item.urgency_id, digest: item.icon!.digest }));
}
