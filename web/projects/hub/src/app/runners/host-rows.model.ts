import {
  type HostRegistryView,
  RunnerConnection,
  type RunnerRegistryView,
  type SubscriptionPace,
  type WorkspaceRow,
  formatSeenAgo,
  hostSubscriptionPaces,
  hostWorkspaceRows,
} from 'fleet';
import type { ChunkSummary } from 'fleet/shell';

import { servedProjects } from '../core/project-serving';

/** One binding line under a hosted runner row: the project it binds, by display name, the
 * workspace it binds it in, and its agents in use of those it declares. `unreported` marks a
 * workspace its host does not report. */
export interface BindingLine {
  readonly project: string;
  readonly workspaceId: string;
  readonly agents: number;
  readonly agentsInUse: number;
  readonly unreported: boolean;
}

/** A binding line plus the environments its live chunks hold — the host workspace table's
 * in-use numerator, summed per workspace. */
interface BindingUse extends BindingLine {
  readonly environmentsInUse: number;
}

/** One host card's own facts, pre-folded for display: its name, its last report, its
 * workspaces with their environments in use, and each subscription once with its spenders. */
export interface HostCard {
  readonly hostId: string;
  readonly hostName: string;
  readonly online: boolean;
  readonly connection: RunnerConnection;
  readonly reportLabel: string;
  readonly workspaces: readonly WorkspaceRow[];
  readonly subscriptionPaces: readonly SubscriptionPace[];
}

/** The runner rows the runners view renders, partitioned: each host card with the rows it
 * holds, then the rows under no card. */
export interface HostGroups<Row extends RunnerRegistryView> {
  readonly cards: readonly { readonly card: HostCard; readonly rows: readonly Row[] }[];
  readonly hostless: readonly Row[];
}

/** Whether `chunk` works the project a binding names — the binding names its project as
 * the runner's declaration entry, so it matches the chunk's id or slug, or the project the
 * hub resolved that entry to. */
function chunkWorksBinding(chunk: ChunkSummary, project: string, resolvedId: string | null): boolean {
  if (chunk.project_id === project || chunk.project_slug === project) return true;
  return resolvedId !== null && chunk.project_id === resolvedId;
}

/** Each of `runner`'s bindings joined to the board's live chunks it holds on that binding's
 * project — a chunk counts toward the first binding it matches, never two. */
function bindingUses(runner: RunnerRegistryView, chunks: readonly ChunkSummary[]): readonly BindingUse[] {
  const held = chunks.filter((chunk) => chunk.runner_id === runner.runner_id);
  const claimed = new Set<string>();
  const served = servedProjects(runner);
  return (runner.bindings ?? []).map((binding) => {
    const entry = served.find((s) => s.entry === binding.project);
    const matched = held.filter(
      (chunk) => !claimed.has(chunk.chunk_id) && chunkWorksBinding(chunk, binding.project, entry?.project_id ?? null),
    );
    for (const chunk of matched) claimed.add(chunk.chunk_id);
    return {
      project: entry?.name ?? entry?.slug ?? binding.project,
      workspaceId: binding.workspace_id,
      agents: binding.agents,
      agentsInUse: matched.length,
      unreported: binding.workspace_unreported ?? false,
      environmentsInUse: matched.reduce((sum, chunk) => sum + (chunk.environment_count ?? 0), 0),
    };
  });
}

/** `runner`'s binding lines, each with its agents in use from the board's live chunks. */
export function bindingLines(runner: RunnerRegistryView, chunks: readonly ChunkSummary[]): readonly BindingLine[] {
  return bindingUses(runner, chunks).map((use) => ({
    project: use.project,
    workspaceId: use.workspaceId,
    agents: use.agents,
    agentsInUse: use.agentsInUse,
    unreported: use.unreported,
  }));
}

/** The display name of `runner`'s one subscription, resolved from its host's roster — the
 * slug itself where the roster names no such subscription; `null` for none. */
export function subscriptionName(
  runner: RunnerRegistryView,
  hosts: ReadonlyMap<string, HostRegistryView>,
): string | null {
  const slug = runner.subscription ?? null;
  if (slug === null) return null;
  const host = runner.host_id ? hosts.get(runner.host_id) : undefined;
  return host?.subscriptions?.find((declared) => declared.slug === slug)?.name ?? slug;
}

/** Environments in use per workspace across `runners`, from their bindings' live chunks. */
function workspaceInUse(runners: readonly RunnerRegistryView[], chunks: readonly ChunkSummary[]): Map<string, number> {
  const inUse = new Map<string, number>();
  for (const runner of runners) {
    for (const use of bindingUses(runner, chunks)) {
      inUse.set(use.workspaceId, (inUse.get(use.workspaceId) ?? 0) + use.environmentsInUse);
    }
  }
  return inUse;
}

/**
 * Each host folded to its card, in the hosts read's order. `runners` is the whole registry,
 * unlensed, so a card's in-use counts and spender names never shrink under the project lens;
 * `lensed` is the runners the lens shows, or `null` on All. Under a lens a card holding no
 * lensed runner is hidden; on All every host shows, one with no runners included.
 */
export function foldHostCards(
  hosts: readonly HostRegistryView[],
  runners: readonly RunnerRegistryView[],
  lensed: readonly RunnerRegistryView[] | null,
  chunks: readonly ChunkSummary[],
  nowMs: number,
): readonly HostCard[] {
  const names = new Map(runners.map((runner) => [runner.runner_id, runner.runner_name]));
  const shownHosts = lensed === null ? null : new Set(lensed.map((runner) => runner.host_id));
  return hosts
    .filter((host) => shownHosts === null || shownHosts.has(host.host_id))
    .map((host) => ({
      hostId: host.host_id,
      hostName: host.host_name,
      online: host.online,
      connection: host.connection,
      reportLabel:
        host.connection === RunnerConnection.NEVER_CONNECTED
          ? 'never reported'
          : formatSeenAgo(host.last_seen_at, host.online, nowMs),
      workspaces: hostWorkspaceRows(
        host,
        workspaceInUse(
          runners.filter((runner) => runner.host_id === host.host_id),
          chunks,
        ),
      ),
      subscriptionPaces: hostSubscriptionPaces(host, nowMs, (id) => names.get(id) ?? id),
    }));
}

/** `rows` partitioned under `cards`: each card with the rows its host holds, in row order,
 * then every row under no shown card — host-less, or under a host the read does not carry. */
export function groupHostRows<Row extends RunnerRegistryView>(
  rows: readonly Row[],
  cards: readonly HostCard[],
): HostGroups<Row> {
  const hostIds = new Set(cards.map((card) => card.hostId));
  return {
    cards: cards.map((card) => ({ card, rows: rows.filter((row) => row.host_id === card.hostId) })),
    hostless: rows.filter((row) => !row.host_id || !hostIds.has(row.host_id)),
  };
}
