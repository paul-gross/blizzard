import { type runnerApi, type SseStatus, type StatCell } from 'fleet';

/** The header's connection label: the live stream's reconnect first, then its settled auth failure, then the
 * dashboard read's pending and failed states — `ok` otherwise. */
export function headerConnectionLabel(
  streamState: SseStatus,
  authFailed: boolean,
  dashboardPending: boolean,
  dashboardError: boolean,
): string {
  if (streamState === 'reconnecting') return 'reconnecting…';
  if (authFailed) return 'degraded';
  if (dashboardPending) return 'connecting…';
  if (dashboardError) return 'offline';
  return 'ok';
}

/** The header's live stat cells — environments in use over the pool's size, active agent leases over
 * `max_agents` — withheld (`[]`) while the dashboard read is still pending. */
export function headerStatCells(
  dashboardPending: boolean,
  dashboard: runnerApi.DashboardView | undefined,
): readonly StatCell[] {
  if (dashboardPending) return [];
  const envs = dashboard?.environments?.items ?? [];
  const envsUsed = envs.filter((env) => env.chunk_id != null).length;
  const capacities = dashboard?.runner?.capacities;
  return [
    { key: 'envs', label: 'Envs', value: envsUsed, capacity: envs.length },
    { key: 'agents', label: 'Agents', value: capacities?.used ?? 0, capacity: capacities?.max_agents ?? 0 },
  ];
}
