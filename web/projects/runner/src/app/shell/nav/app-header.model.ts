import { type runnerApi, type SseStatus, type StatCell } from 'fleet';

/** The header's connection label: the live stream's reconnect first, then its settled auth failure, then the
 * host read's pending and failed states — `ok` otherwise. */
export function headerConnectionLabel(
  streamState: SseStatus,
  authFailed: boolean,
  hostPending: boolean,
  hostError: boolean,
): string {
  if (streamState === 'reconnecting') return 'reconnecting…';
  if (authFailed) return 'degraded';
  if (hostPending) return 'connecting…';
  if (hostError) return 'offline';
  return 'ok';
}

/** The header's live stat cells, across the whole host — environments in use over every workspace's environments,
 * agents in use over every serving runner's `max_agents` — withheld (`[]`) while the host's runner read is still
 * pending. */
export function headerStatCells(
  hostPending: boolean,
  runners: readonly runnerApi.HostRunnerView[] | undefined,
  environments: readonly runnerApi.HostWorkspaceEnvironmentsView[] | undefined,
): readonly StatCell[] {
  if (hostPending) return [];
  const envs = (environments ?? []).flatMap((workspace) => workspace.items ?? []);
  const envsUsed = envs.filter((env) => env.held ?? env.chunk_id != null).length;
  const serving = (runners ?? []).filter((runner) => runner.state === 'serving');
  return [
    { key: 'envs', label: 'Envs', value: envsUsed, capacity: envs.length },
    {
      key: 'agents',
      label: 'Agents',
      value: serving.reduce((sum, runner) => sum + (runner.agents_in_use ?? 0), 0),
      capacity: serving.reduce((sum, runner) => sum + (runner.max_agents ?? 0), 0),
    },
  ];
}
