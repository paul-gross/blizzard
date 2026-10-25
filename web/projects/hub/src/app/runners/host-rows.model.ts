import {
  type HostRegistryView,
  RunnerConnection,
  type RunnerRegistryView,
  type SubscriptionPace,
  type WorkspaceRow,
  ageMs,
  compactRef,
  formatAbsolute,
  formatSeenTag,
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

/** A row carrying binding lines — a runner row, or anything shaped like one — and the agent count its runner
 * registered, absent for a previous-minor runner. */
interface BoundRow {
  readonly agents?: number | null;
  readonly bindingLines: readonly BindingLine[];
}

/** A runner's declared agents: the count it registered, else the sum of its binding lines' (a previous-minor
 * runner declares per binding); `null` when it declares none. */
function runnerAgents(row: BoundRow): number | null {
  if (row.agents != null) return row.agents;
  return row.bindingLines.length === 0 ? null : row.bindingLines.reduce((sum, line) => sum + line.agents, 0);
}

/** The agents `rows` declare — the sum of their runners' declared agents; `null` for none declared. */
export function declaredAgents(rows: readonly BoundRow[]): number | null {
  const declared = rows.map(runnerAgents).filter((agents): agents is number => agents !== null);
  return declared.length === 0 ? null : declared.reduce((sum, agents) => sum + agents, 0);
}

/** The agents busy across `rows` — the sum of their binding lines' agents in use. */
export function busyAgents(rows: readonly BoundRow[]): number {
  return rows.flatMap((row) => row.bindingLines).reduce((sum, line) => sum + line.agentsInUse, 0);
}

/** A binding line plus the environments its live chunks hold — the host workspace table's
 * in-use numerator, summed per workspace. */
interface BindingUse extends BindingLine {
  readonly environmentsInUse: number;
  readonly chunks: readonly ChunkSummary[];
}

/** A live chunk holding environments in one host workspace — the hub knows the chunk, its
 * step and its runner, but not which environments it holds. */
export interface HeldChunk {
  readonly chunkId: string;
  readonly shortRef: string;
  readonly step: string;
  readonly runnerName: string;
  readonly environments: number;
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
  /** The host's own wire facts, for the Fleet tab's Connection section. */
  readonly url: string | null;
  readonly enrolled: boolean;
  readonly registeredAt: string | null;
  readonly addedAt: string;
  readonly addedBy: string | null;
  readonly lastSeenLabel: string;
  /** The age tag of the host's last report (`[4s]`); `null` when no age can be read — it
   * never reported, or its instant is unreadable or ahead of this clock. */
  readonly reportedAgo: string | null;
  /** The live chunks each workspace holds, by workspace id; a workspace holding none is absent. */
  readonly heldChunks: ReadonlyMap<string, readonly HeldChunk[]>;
}

/** The runner rows the runners view renders, partitioned: each host card with the rows it
 * holds, then the rows under no card. */
export interface HostGroups<Row extends RunnerRegistryView> {
  readonly cards: readonly { readonly card: HostCard; readonly rows: readonly Row[] }[];
  readonly hostless: readonly Row[];
}

/** Whether `chunk` was claimed for a binding: in the binding's workspace, on the project the
 * binding names. The binding names its project as the runner's declaration entry, so it matches
 * the chunk's id or slug, or the project the hub resolved that entry to. */
function chunkWorksBinding(chunk: ChunkSummary, workspaceId: string, project: string, resolvedId: string | null): boolean {
  if (chunk.workspace_id !== workspaceId) return false;
  if (chunk.project_id === project || chunk.project_slug === project) return true;
  return resolvedId !== null && chunk.project_id === resolvedId;
}

/** Each of `runner`'s bindings joined to the board's live chunks it holds on that binding — the
 * chunks the runner holds, claimed in the binding's workspace, on its project. A chunk whose
 * workspace matches no binding counts toward none. */
function bindingUses(runner: RunnerRegistryView, chunks: readonly ChunkSummary[]): readonly BindingUse[] {
  const held = chunks.filter((chunk) => chunk.runner_id === runner.runner_id);
  const served = servedProjects(runner);
  return (runner.bindings ?? []).map((binding) => {
    const entry = served.find((s) => s.entry === binding.project);
    const matched = held.filter((chunk) =>
      chunkWorksBinding(chunk, binding.workspace_id, binding.project, entry?.project_id ?? null),
    );
    return {
      project: entry?.name ?? entry?.slug ?? binding.project,
      workspaceId: binding.workspace_id,
      agents: binding.agents,
      agentsInUse: matched.length,
      unreported: binding.workspace_unreported ?? false,
      environmentsInUse: matched.reduce((sum, chunk) => sum + (chunk.environment_count ?? 0), 0),
      chunks: matched,
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

/** The live chunks `runners` hold per workspace, from their bindings. */
function workspaceHeld(
  runners: readonly RunnerRegistryView[],
  chunks: readonly ChunkSummary[],
): Map<string, readonly HeldChunk[]> {
  const held = new Map<string, HeldChunk[]>();
  for (const runner of runners) {
    for (const use of bindingUses(runner, chunks)) {
      const lines = held.get(use.workspaceId) ?? [];
      for (const chunk of use.chunks) {
        lines.push({
          chunkId: chunk.chunk_id,
          shortRef: compactRef(chunk.chunk_id),
          step: chunk.current_node_name ?? chunk.current_node_id ?? '—',
          runnerName: runner.runner_name,
          environments: chunk.environment_count ?? 0,
        });
      }
      held.set(use.workspaceId, lines);
    }
  }
  return held;
}

function reportedAgo(lastSeenAt: string | null | undefined, nowMs: number): string | null {
  const delta = ageMs(lastSeenAt, nowMs);
  return delta === null ? null : formatSeenTag(lastSeenAt, nowMs);
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
    .map((host) => {
      const hostRunners = runners.filter((runner) => runner.host_id === host.host_id);
      return {
        hostId: host.host_id,
        hostName: host.host_name,
        online: host.online,
        connection: host.connection,
        reportLabel:
          host.connection === RunnerConnection.NEVER_CONNECTED
            ? 'never reported'
            : formatSeenAgo(host.last_seen_at, host.online, nowMs),
        workspaces: hostWorkspaceRows(host, workspaceInUse(hostRunners, chunks)),
        subscriptionPaces: hostSubscriptionPaces(host, nowMs, (id) => names.get(id) ?? id),
        url: host.url ?? null,
        enrolled: host.enrolled,
        registeredAt: host.registered_at ?? null,
        addedAt: host.added_at,
        addedBy: host.added_by ?? null,
        lastSeenLabel: host.last_seen_at ? formatAbsolute(host.last_seen_at) : 'never',
        reportedAgo: reportedAgo(host.last_seen_at, nowMs),
        heldChunks: workspaceHeld(hostRunners, chunks),
      };
    });
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
