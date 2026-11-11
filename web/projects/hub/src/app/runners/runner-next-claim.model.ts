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

export function nextUrgencyIcon(next: hubApi.NextClaimResponse | undefined, urgencies: readonly hubApi.UrgencyView[] | undefined): { key: string; digest: string } | null {
  const urgency = urgencies?.find((item) => item.urgency_id === next?.next?.urgency?.urgency_id);
  return urgency && !urgency.emoji && urgency.icon ? { key: urgency.urgency_id, digest: urgency.icon.digest } : null;
}
