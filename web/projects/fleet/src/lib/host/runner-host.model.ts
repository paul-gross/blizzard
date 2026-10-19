import type * as runnerApi from '../api/runner';
import { compactRef } from '../core/compact-ref';
import { SampleMissReason } from '../api/hub';
import { ageMs, formatAge, formatHeldFor, formatRefreshedAgo, formatSeenTag } from '../core/when';
import { type SubscriptionLine, subscriptionLine } from './subscription-blocks.model';
import { classifySubscriptionFreshness, type SubscriptionPace, toPaceBars } from './subscription-pace.model';
import type { EnvironmentRow, WorkspaceRow } from './workspace-table';

/**
 * `42m` since the binding fact — browser-clock decoration only
 * (`bzh:utc-instants` via `ageMs`): a skew-broken timestamp renders `—`, and an
 * unheld environment (no `held_since`) renders blank rather than `—`.
 */
export function heldForLabel(heldSince: string | null | undefined, now: number): string {
  if (heldSince == null) return '';
  const age = ageMs(heldSince, now);
  return age === null ? '—' : formatHeldFor(age);
}

/** Who holds a held environment — its runner's display name and the project of the chunk
 * holding it, `null` for an unheld one or one whose host names no runner. */
function holderLabel(env: runnerApi.HostEnvironmentView, runnerName: (runnerId: string) => string): string | null {
  if (env.runner_id == null) return null;
  return [runnerName(env.runner_id), env.project].filter((part) => part != null && part !== '').join(' · ');
}

/** One {@link EnvironmentRow} per environment of a workspace, in wire order, aged against
 * `now` — `runnerName` resolves a holder's runner id to its display name. */
export function runnerEnvironmentRows(
  envs: readonly runnerApi.HostEnvironmentView[],
  now: number,
  runnerName: (runnerId: string) => string,
): readonly EnvironmentRow[] {
  return envs.map((env) => ({
    environmentId: env.environment_id,
    isHeld: env.held ?? env.chunk_id != null,
    chunkRef: env.chunk_id == null ? '' : compactRef(env.chunk_id),
    heldFor: heldForLabel(env.held_since, now),
    holder: holderLabel(env, runnerName),
  }));
}

/** The host's environments grouped by workspace, as the shared workspace table's rows —
 * one per declared workspace in `workspaces`' order, its capacity the workspace's own
 * `max_environments`. A group naming a workspace the declaration list does not carry
 * still renders, after the declared ones, with no capacity. */
export function runnerWorkspaceRows(
  workspaces: readonly runnerApi.HostWorkspaceView[],
  groups: readonly runnerApi.HostWorkspaceEnvironmentsView[],
  now: number,
  runnerName: (runnerId: string) => string,
): readonly WorkspaceRow[] {
  const byId = new Map(groups.map((group) => [group.workspace_id, group.items ?? []]));
  const declared = new Set(workspaces.map((ws) => ws.workspace_id));
  const row = (workspaceId: string, capacity: number | null): WorkspaceRow => {
    const environments = runnerEnvironmentRows(byId.get(workspaceId) ?? [], now, runnerName);
    return {
      workspaceId,
      capacity,
      inUse: environments.filter((env) => env.isHeld).length,
      flag: null,
      environments,
    };
  };
  return [
    ...workspaces.map((ws) => row(ws.workspace_id, ws.max_environments)),
    ...groups.filter((group) => !declared.has(group.workspace_id)).map((group) => row(group.workspace_id, null)),
  ];
}

/** Operator-facing text per closed-set miss reason — what to do about it, not the machine word. */
export const MISS_REASON_TEXT: Readonly<Record<runnerApi.SampleMissReason, string>> = {
  credential_lapsed: 'credential lapsed: log in again',
  credential_unreadable: 'credential unreadable',
  endpoint_unreachable: 'endpoint unreachable',
  response_unparseable: 'response unparseable',
};

/** Operator-facing text per closed-set renewal failure reason. */
export const RENEWAL_FAILURE_TEXT: Readonly<Record<runnerApi.RenewalFailureReason, string>> = {
  renewer_unavailable: 'vendor CLI unavailable',
  timed_out: 'timed out',
  vendor_refused: 'vendor refused',
  protocol_error: 'unreadable vendor response',
};

/** `-34s ago` since the newest sampling attempt, or `never` when there is none or it is skew-broken. */
export function sampledAgoLabel(sampledAt: string | null, now: number): string {
  const age = ageMs(sampledAt, now);
  return age === null ? 'never' : `${formatAge(age)} ago`;
}

/** "ok", "never sampled", or "miss: <operator text>" — the operator-facing distinguishable
 * condition, one string per wire `SampleMissReason`. */
export function conditionLabel(ok: boolean | null, missReason: runnerApi.SampleMissReason | null): string {
  if (ok === null) return 'never sampled';
  if (ok) return 'ok';
  return `miss: ${missReason === null ? 'unknown' : MISS_REASON_TEXT[missReason]}`;
}

/** The newest credential renewal in operator words, aged against `now` — `null` when the
 * subscription was never renewed. Reads the wire's typed result and reason; parses nothing. */
export function renewalLabel(
  result: runnerApi.RenewalResult | null,
  failureReason: runnerApi.RenewalFailureReason | null,
  attemptedAt: string | null,
  now: number,
): string | null {
  if (result === null) return null;
  const ago = sampledAgoLabel(attemptedAt, now);
  switch (result) {
    case 'renewed':
      return `renewed ${ago}`;
    case 'failed':
      return `failed (${failureReason === null ? 'unknown cause' : RENEWAL_FAILURE_TEXT[failureReason]}) ${ago}`;
    case 'unrecorded':
      return `attempted ${ago}, outcome not recorded`;
  }
}

/** A subscription's newest reading is a lapsed credential — log in again. */
function lapsedNow(sub: runnerApi.HostSubscriptionView): boolean {
  return sub.ok === false && sub.miss_reason === 'credential_lapsed';
}

/** One shared subscription row per declared subscription, in wire order, aged against
 * `now`: its newest sampled snapshot's windows as pace bars, refreshed against that snapshot,
 * naming the runners spending it — `runnerName` resolves a spender's id to its display name.
 * A lapsed credential reads as lapsed; a subscription attempted but never sampled carries its
 * {@link SubscriptionPace.sample} line in place of the bars; one never attempted has no sample yet. */
export function runnerSubscriptionPaces(
  subs: readonly runnerApi.HostSubscriptionView[],
  now: number,
  runnerName: (runnerId: string) => string,
): readonly SubscriptionPace[] {
  return subs.map((sub) => {
    const windowsSampledAt = sub.windows_sampled_at ?? null;
    const delta = windowsSampledAt === null ? null : ageMs(windowsSampledAt, now);
    const lapsed = lapsedNow(sub);
    const ok = sub.ok ?? null;
    const missReason = sub.miss_reason ?? null;
    return {
      slug: sub.slug,
      name: sub.name,
      provider: sub.provider,
      paceBars: toPaceBars(now, sub.windows ?? []),
      condition: lapsed ? SampleMissReason.CREDENTIAL_LAPSED : null,
      sampledAt: windowsSampledAt,
      refreshedLabel: delta === null ? null : formatRefreshedAgo(delta),
      refreshedTag: delta === null ? null : formatSeenTag(windowsSampledAt, now),
      freshness: delta === null ? null : classifySubscriptionFreshness(delta),
      missReason: ok === false ? missReason : null,
      sample:
        windowsSampledAt === null && !lapsed && ok !== null
          ? {
              conditionLabel: conditionLabel(ok, missReason),
              sampledAgo: sampledAgoLabel(sub.sampled_at ?? null, now),
              ok,
            }
          : null,
      spenders: (sub.spenders ?? []).map(runnerName),
      renewalLabel: renewalLabel(
        sub.renewal_result ?? null,
        sub.renewal_failure_reason ?? null,
        sub.renewal_attempted_at ?? null,
        now,
      ),
    };
  });
}

/** One line of blocks per declared subscription, in wire order, aged against `now`: its newest
 * sampled snapshot's windows, paced; a lapsed credential reads `lapsed`; one never sampled carries
 * its newest attempt's condition as the line's note, or the dash when never attempted. */
export function runnerSubscriptionLines(subs: readonly runnerApi.HostSubscriptionView[], now: number): readonly SubscriptionLine[] {
  return runnerSubscriptionPaces(subs, now, (id) => id).map((pace) => ({
    ...subscriptionLine(pace),
    note: pace.sample?.conditionLabel ?? null,
  }));
}
