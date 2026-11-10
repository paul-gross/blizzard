import { type runnerApi, type Tone } from 'fleet';

import type { RunnerDashboard } from '../../core/panel-reads.model';
import { identityRefusal } from '../../core/runner-health.model';

/** Something about one runner's link the operator has to act on. */
export interface HubIssue {
  readonly key: string;
  readonly runner: string;
  readonly text: string;
  /** The remedy or the hub's reason, for the line's hover. */
  readonly title: string | null;
  /** The line's colour: `stale` for a dead link, `needs` for a refusal or conflict, `idle` for a state read raw. */
  readonly tone: Tone;
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

/** Folds every runner's dashboard into one {@link HubSummary}; `null` before any dashboard lands. */
export function hubSummary(dashboards: readonly RunnerDashboard[]): HubSummary | null {
  const statuses = dashboards.flatMap(({ owner, dashboard }) => (dashboard.runner?.hub ? [{ owner, status: dashboard.runner }] : []));
  if (statuses.length === 0) return null;
  const issues: HubIssue[] = [];
  for (const { owner, status } of statuses) {
    if (!status.hub.reachable) {
      issues.push({ key: `${owner.runnerId}:link`, runner: owner.label, text: 'cannot reach the hub', title: status.hub.endpoint, tone: 'stale' });
    }
    const refusal = identityRefusal(status.identity);
    if (refusal) {
      issues.push({ key: `${owner.runnerId}:identity`, runner: owner.label, text: refusal.issue, title: refusal.title, tone: refusal.tone });
    }
    for (const conflict of status.routing_conflicts ?? []) {
      issues.push({
        key: `${owner.runnerId}:conflict:${conflict.project_id}`,
        runner: owner.label,
        text: `${conflict.entries.join(', ')} name one project — claiming nothing`,
        title: conflict.project_id,
        tone: 'needs',
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
