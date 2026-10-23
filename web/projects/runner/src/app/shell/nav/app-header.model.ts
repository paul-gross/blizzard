import { type runnerApi, type SseStatus, type StatCell } from 'fleet/runner-shell';

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
 * agents in use over every serving runner's `max_agents` — or, with `runnerName` set, across that runner: the
 * environments it holds over every environment, and its own agents. Withheld (`[]`) while either of the host's reads —
 * the runners or the environments — is still pending, so a half-landed fold never reads as `Envs 0/0`. */
export function headerStatCells(
  hostPending: boolean,
  runners: readonly runnerApi.HostRunnerView[] | undefined,
  environments: readonly runnerApi.HostWorkspaceEnvironmentsView[] | undefined,
  runnerName: string | null = null,
): readonly StatCell[] {
  if (hostPending) return [];
  const scoped = (runners ?? []).filter((runner) => runnerName === null || runner.name === runnerName);
  const scopedIds = new Set(scoped.map((runner) => runner.runner_id));
  const envs = (environments ?? []).flatMap((workspace) => workspace.items);
  const envsUsed = envs.filter(
    (env) => env.held && (runnerName === null || scopedIds.has(env.runner_id ?? null)),
  ).length;
  const serving = scoped.filter((runner) => runner.state === 'serving');
  return [
    { key: 'envs', label: 'Envs', value: envsUsed, capacity: envs.length },
    {
      key: 'agents',
      label: 'Agents',
      value: serving.reduce((sum, runner) => sum + runner.agents_in_use, 0),
      capacity: serving.reduce((sum, runner) => sum + runner.max_agents, 0),
    },
  ];
}
