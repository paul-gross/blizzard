import type { hubApi, Urgency } from 'fleet';

/** Presentation of the hub's bounded walk, without making a claimability judgment. */
export function nextClaimSummary(next: hubApi.NextClaimResponse | hubApi.NextClaimScenario) {
  return {
    next: next.next,
    passed: next.passed,
    omitted: Math.max(0, next.passed_total - next.passed.length),
    unserved: next.unserved,
    ifHolding: next.if_holding,
    heldAt: next.held_at,
  };
}

/** Every scenario the hub reports: the response's own account, then the no-target alternative when present. */
function scenarios(next: hubApi.NextClaimResponse | undefined): (hubApi.NextClaimResponse | hubApi.NextClaimScenario)[] {
  return next ? [next, ...(next.without_repository_targets ? [next.without_repository_targets] : [])] : [];
}

export function prerequisiteIds(next: hubApi.NextClaimResponse | undefined): string[] {
  return [...new Set(scenarios(next).flatMap((scenario) => [...scenario.passed, ...scenario.held_at])
    .map((entry) => entry.prerequisite).filter((id): id is string => id !== null))];
}

export function prerequisiteStatuses(results: readonly { data?: hubApi.ChunkDetail; isError?: boolean }[]): Readonly<Record<string, string>> {
  return Object.fromEntries(results.filter((result) => result.data && !result.isError).map((result) => [result.data!.chunk_id, result.data!.status]));
}

export function claimUrgencyIcons(next: hubApi.NextClaimResponse | undefined, urgencies: readonly Urgency[] | undefined): { key: string; digest: string }[] {
  const ids = new Set(scenarios(next).flatMap((scenario) => [scenario.next, scenario.if_holding, ...scenario.passed, ...scenario.held_at]).map((entry) => entry?.urgency?.urgency_id));
  return (urgencies ?? []).filter((item) => ids.has(item.id) && !item.default && !item.emoji && item.icon)
    .map((item) => ({ key: item.id, digest: item.icon!.digest }));
}
