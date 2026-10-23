import type { HostRegistryView, HostSubscriptionUsageView, SubscriptionUsageView } from '../api/hub';
import { ageMs, formatRefreshedAgo, formatSeenTag } from '../core/when';
import { classifySubscriptionFreshness, type SubscriptionPace, toPaceBars } from './subscription-pace.model';
import type { WorkspaceRow } from './workspace-rows.model';

/** The hub's subscription usage samples folded to the shared subscription list's rows, in
 * wire order, every time-relative field read against the one `nowMs`. */
export function hubSubscriptionPaces(
  usage: readonly SubscriptionUsageView[],
  nowMs: number,
): readonly SubscriptionPace[] {
  return usage.map((s) => {
    const sampledAt = s.sampled_at ?? null;
    const delta = sampledAt === null ? null : ageMs(sampledAt, nowMs);
    return {
      slug: s.slug,
      name: s.name,
      paceBars: toPaceBars(nowMs, s.windows),
      condition: s.condition ?? null,
      sampledAt,
      refreshedLabel: delta === null ? null : formatRefreshedAgo(delta),
      refreshedTag: delta === null ? null : formatSeenTag(sampledAt, nowMs),
      freshness: delta === null ? null : classifySubscriptionFreshness(delta),
      missReason: s.miss_reason ?? null,
    };
  });
}

/** A host's subscriptions, each once, naming the runners that spend it — `runnerName`
 * resolves a spender id to its display name. */
export function hostSubscriptionPaces(
  host: HostRegistryView,
  nowMs: number,
  runnerName: (runnerId: string) => string,
): readonly SubscriptionPace[] {
  const usage: readonly HostSubscriptionUsageView[] = host.subscription_usage ?? [];
  const paces = hubSubscriptionPaces(usage, nowMs);
  return paces.map((pace, i) => ({ ...pace, spenders: (usage[i].runners ?? []).map(runnerName) }));
}

/** A host's reported workspaces, in wire order, each with its environments in use —
 * `inUse` maps a workspace id to the environments its runners' live chunks hold; one it
 * does not name has none in use. The hub reports no per-environment rows. */
export function hostWorkspaceRows(host: HostRegistryView, inUse: ReadonlyMap<string, number>): readonly WorkspaceRow[] {
  return (host.workspaces ?? []).map((ws) => ({
    workspaceId: ws.workspace_id,
    capacity: ws.env_capacity ?? null,
    inUse: inUse.get(ws.workspace_id) ?? 0,
    flag: null,
    environments: [],
  }));
}
