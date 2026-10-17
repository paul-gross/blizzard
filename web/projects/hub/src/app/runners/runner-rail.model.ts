import { busyAgents, declaredAgents, type HostGroups } from './host-rows.model';
import type { RunnerRow } from './runner-rows.model';

/** A used-over-total gauge. */
export interface Gauge {
  readonly used: number;
  readonly total: number;
}

/** One runner on the rail: the row, and its agents in use of those it declares. */
export interface RailRow {
  readonly row: RunnerRow;
  /** `null` when the runner declares no bindings, as a host-less runner has none. */
  readonly agents: Gauge | null;
}

/** What the board's rail draws: the fleet-wide environment strip and one line group per runner. */
export interface RailVm {
  /** `null` when no runner or workspace reports a capacity. */
  readonly envs: Gauge | null;
  readonly rows: readonly RailRow[];
}

/**
 * The fleet-wide environment gauge: every host workspace's in-use and capacity, plus each
 * host-less runner's own pool. Anything reporting no capacity adds to neither number.
 */
export function envGauge(groups: HostGroups<RunnerRow>): Gauge | null {
  let used = 0;
  let total = 0;
  for (const { card } of groups.cards) {
    for (const ws of card.workspaces) {
      if (ws.capacity === null) continue;
      total += ws.capacity;
      used += ws.inUse;
    }
  }
  for (const row of groups.hostless) {
    if (row.env_capacity === null || row.env_capacity === undefined) continue;
    total += row.env_capacity;
    used += row.used;
  }
  return total === 0 ? null : { used, total };
}

/** The agents a runner has busy of those it declares; `null` when it declares none. */
export function agentGauge(row: RunnerRow): Gauge | null {
  const declared = declaredAgents([row]);
  return declared === null ? null : { used: busyAgents([row]), total: declared };
}

/** The rail's view model: the env strip, then the hosted runners in card order and the host-less after. */
export function railVm(groups: HostGroups<RunnerRow>): RailVm {
  const rows = [...groups.cards.flatMap((group) => group.rows), ...groups.hostless];
  return { envs: envGauge(groups), rows: rows.map((row) => ({ row, agents: agentGauge(row) })) };
}
