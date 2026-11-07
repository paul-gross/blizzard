import { type SubscriptionLine, subscriptionLine, type SubscriptionPace } from 'fleet';

import { type ConnectionLight, runnerLight } from './connection-status.model';
import { busyAgents, declaredAgents, type HostGroups } from './host-rows.model';
import type { RunnerRow } from './runner-rows.model';

/** A used-over-total gauge, named for what it gauges. */
export interface Gauge {
  readonly name: string;
  readonly used: number;
  readonly total: number;
}

/** One runner on the rail: the row, its light, the Fleet tab selection that opens it, and one
 * agent gauge per project it binds. */
export interface RailRow {
  readonly row: RunnerRow;
  readonly light: ConnectionLight;
  /** Its host's id when it sits under a host card, else its own — the Fleet tab selects hosts and host-less runners. */
  readonly fleetId: string;
  /** The host it runs on; `null` for a host-less runner. */
  readonly hostName: string | null;
  /** Empty when the runner declares no bindings, as a host-less runner has none. */
  readonly agents: readonly Gauge[];
}

/** What the board's rail draws: one environment strip per pool, one line per distinct
 * subscription, and one line group per runner. */
export interface RailVm {
  /** Empty when no runner or workspace reports a capacity. */
  readonly envs: readonly Gauge[];
  readonly subscriptions: readonly RailSubscription[];
  readonly rows: readonly RailRow[];
}

/**
 * One environment gauge per pool: each host workspace, then each host-less runner's own pool,
 * named for its workspace. A pool reporting no capacity, or a zero one, draws no gauge.
 */
export function envGauges(groups: HostGroups<RunnerRow>): readonly Gauge[] {
  const gauges: Gauge[] = [];
  for (const { card } of groups.cards) {
    for (const ws of card.workspaces) {
      if (!ws.capacity) continue;
      gauges.push({ name: ws.workspaceId ?? card.hostName, used: ws.inUse, total: ws.capacity });
    }
  }
  for (const row of groups.hostless) {
    if (!row.env_capacity) continue;
    gauges.push({ name: row.workspace_id ?? row.runner_name, used: row.used, total: row.env_capacity });
  }
  return gauges;
}

/** One subscription line on the rail, kept apart by the host whose spenders it reaches: a
 * subscription reaches only its own host's spenders, so two hosts sharing a slug read as two lines. */
export interface RailSubscription extends SubscriptionLine {
  /** Unique per host and slug (or per host-less runner and slug) — the list's track key. */
  readonly key: string;
  /** The host the reading belongs to; `null` for a host-less runner's own. */
  readonly hostName: string | null;
}

/**
 * One line per subscription per host, then per host-less runner, in first-seen order — a lapsed
 * login on one host never outranks, or hides, another host's reading.
 */
export function subscriptionLines(groups: HostGroups<RunnerRow>): readonly RailSubscription[] {
  const lines: RailSubscription[] = [];
  const seen = new Set<string>();
  const add = (owner: string, hostName: string | null, paces: readonly SubscriptionPace[]): void => {
    for (const pace of paces) {
      const key = `${owner}\u0000${pace.slug}`;
      if (seen.has(key)) continue;
      seen.add(key);
      lines.push({ ...subscriptionLine(pace), key, hostName });
    }
  };
  for (const { card } of groups.cards) add(`host:${card.hostId}`, card.hostName, card.subscriptionPaces);
  for (const row of groups.hostless) add(`runner:${row.runner_id}`, null, row.subscriptionPaces);
  return lines;
}

/** A runner's agents: one gauge, its agents busy of the count it shares across its bindings; none where it declares none. */
export function agentGauges(row: RunnerRow): readonly Gauge[] {
  const declared = declaredAgents([row]);
  return declared === null ? [] : [{ name: 'agents', used: busyAgents([row]), total: declared }];
}

/** The rail's view model: the env strips, the subscriptions, then the hosted runners in card order and the host-less after. */
export function railVm(groups: HostGroups<RunnerRow>): RailVm {
  const hosted = groups.cards.flatMap(({ card, rows }) => rows.map((row) => ({ row, fleetId: card.hostId, hostName: card.hostName })));
  const hostless = groups.hostless.map((row) => ({ row, fleetId: row.runner_id, hostName: null }));
  return {
    envs: envGauges(groups),
    subscriptions: subscriptionLines(groups),
    rows: [...hosted, ...hostless].map((entry) => ({ ...entry, light: runnerLight(entry.row), agents: agentGauges(entry.row) })),
  };
}
