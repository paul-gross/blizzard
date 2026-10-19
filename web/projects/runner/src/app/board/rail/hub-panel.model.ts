import { compactRef, type runnerApi } from 'fleet';

import type { RunnerDashboard } from '../../core/panel-reads.model';

/** Something about one runner's link the operator has to act on. */
export interface HubIssue {
  readonly key: string;
  readonly runner: string;
  readonly text: string;
  /** The remedy or the hub's reason, for the line's hover. */
  readonly title: string | null;
}

/** The host's runners' hub links folded into one reading — the hub, whether they reach it, and only what is wrong. */
export interface HubSummary {
  /** The hub the runners report to — the first runner's, the host naming one hub for all of them. */
  readonly endpoint: string | null;
  readonly reachable: number;
  readonly total: number;
  /** The newest contact any runner made, `null` before any. */
  readonly lastContactAt: string | null;
  /** Events every runner holds for the hub, summed. */
  readonly buffered: number;
  readonly issues: readonly HubIssue[];
}

/** The identity verdict's problem in operator words, `null` while it checks out or goes unchecked. */
function identityIssue(identity: runnerApi.IdentityVerdictView | null | undefined): { text: string; title: string | null } | null {
  switch (identity?.state) {
    case 'mismatched':
      return { text: `refusing — its token belongs to ${compactRef(identity.token_runner_id ?? '')}`, title: identity.reset_command };
    case 'refused':
      return { text: 'the hub refuses its token', title: [identity.reason, identity.reset_command].filter(Boolean).join(' — ') || null };
    case 'not_held':
      return { text: 'halted — not held by its host', title: identity.reason ?? null };
    case 'tenant_differs':
      return { text: "halted — tenant differs from its host's", title: identity.reason ?? null };
    default:
      return null;
  }
}

/** Folds every runner's dashboard into one {@link HubSummary}; `null` before any dashboard lands. */
export function hubSummary(dashboards: readonly RunnerDashboard[]): HubSummary | null {
  const statuses = dashboards.flatMap(({ owner, dashboard }) => (dashboard.runner?.hub ? [{ owner, status: dashboard.runner }] : []));
  if (statuses.length === 0) return null;
  const issues: HubIssue[] = [];
  for (const { owner, status } of statuses) {
    if (!status.hub.reachable) {
      issues.push({ key: `${owner.runnerId}:link`, runner: owner.label, text: 'cannot reach the hub', title: status.hub.endpoint });
    }
    const identity = identityIssue(status.identity);
    if (identity) issues.push({ key: `${owner.runnerId}:identity`, runner: owner.label, ...identity });
    for (const conflict of status.routing_conflicts ?? []) {
      issues.push({
        key: `${owner.runnerId}:conflict:${conflict.project_id}`,
        runner: owner.label,
        text: `${conflict.entries.join(', ')} name one project — claiming nothing`,
        title: conflict.project_id,
      });
    }
  }
  const contacts = statuses.flatMap(({ status }) => (status.hub.last_contact_at ? [status.hub.last_contact_at] : [])).sort();
  return {
    endpoint: statuses[0].status.hub.endpoint,
    reachable: statuses.filter(({ status }) => status.hub.reachable).length,
    total: statuses.length,
    lastContactAt: contacts.at(-1) ?? null,
    buffered: statuses.reduce((sum, { status }) => sum + status.hub.buffer_depth, 0),
    issues,
  };
}

/** The fleet counts the first runner whose read carries them reports, `null` while none does. */
export function latestFleetSummary(dashboards: readonly RunnerDashboard[]): runnerApi.FleetSummaryView | null {
  return dashboards.find((entry) => entry.dashboard.fleet_summary != null)?.dashboard.fleet_summary ?? null;
}
