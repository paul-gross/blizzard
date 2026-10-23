import type { runnerApi } from 'fleet';

/** The exact set a floor clear affects, from the host's declaration and runner list. */
export function floorClearScope(
  subscription: string | null,
  runnerId: string,
  runners: readonly runnerApi.HostRunnerView[],
  subscriptions: readonly runnerApi.HostSubscriptionView[],
): string | null {
  const names = new Map(runners.filter((runner) => runner.runner_id !== null).map((runner) => [runner.runner_id, runner.name]));
  if (subscription === null) return names.get(runnerId) ?? runnerId;
  const spenders = subscriptions.find((entry) => entry.slug === subscription)?.spenders;
  if (!spenders?.length) return null;
  const labels = spenders.map((id) => names.get(id) ?? id);
  if (labels.length === 1) return labels[0];
  if (labels.length === 2) return labels.join(' and ');
  return `${labels.slice(0, -1).join(', ')}, and ${labels.at(-1)}`;
}
