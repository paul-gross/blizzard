import type { hubApi } from 'fleet';

import type { EnvTimelineSegment } from './step.model';

/*
 * The env timeline as the tracks hold it. The wire names each block's chunk, runner and node once, in the view's
 * tables; {@link expandTimeline} lays those names back onto every block, so a block reads whole.
 */

export interface EnvTimelineEnvironment extends Omit<hubApi.EnvTimelineEnvironmentView, 'blocks'> {
  readonly segments: readonly EnvTimelineSegment[];
}

export interface EnvTimelineWorkspace extends Omit<hubApi.EnvTimelineWorkspaceView, 'environments'> {
  readonly environments: readonly EnvTimelineEnvironment[];
}

export interface EnvTimelineHost extends Omit<hubApi.EnvTimelineHostView, 'workspaces'> {
  readonly workspaces: readonly EnvTimelineWorkspace[];
}

export interface EnvTimelineView extends Pick<hubApi.EnvTimelineView, 'since' | 'until' | 'generated_at'> {
  readonly hosts: readonly EnvTimelineHost[];
}

/** The view before any answer has landed. */
export const NO_TIMELINE: EnvTimelineView = { since: '', until: '', generated_at: '', hosts: [] };

/** The hub's own now — `generated_at` of its last answer, which may lag or lead the browser clock — or `fallback`
 * before any answer. */
export function hubNow(view: EnvTimelineView | undefined, fallback: number): number {
  return view?.generated_at ? Date.parse(view.generated_at) : fallback;
}

/** The wire's compact block, with its chunk, runner and node named back onto it. */
export function expandTimeline(view: hubApi.EnvTimelineView): EnvTimelineView {
  return {
    since: view.since,
    until: view.until,
    generated_at: view.generated_at,
    hosts: view.hosts.map((host) => ({
      host_id: host.host_id,
      host_name: host.host_name,
      runner_names: host.runner_names,
      workspaces: host.workspaces.map((ws) => ({
        workspace_id: ws.workspace_id,
        environments: ws.environments.map((env) => ({
          environment_id: env.environment_id,
          segments: env.blocks.map((block): EnvTimelineSegment => {
            const chunk = view.chunks[block.chunk_id];
            const node = view.nodes[block.node_id];
            return {
              ...block,
              title: chunk?.title ?? null,
              project_id: chunk?.project_id ?? null,
              runner_name: view.runners[block.runner_id] ?? block.runner_id,
              graph_id: null,
              graph_name: node?.graph_name ?? null,
              node_name: node?.node_name ?? block.node_id,
            };
          }),
        })),
      })),
    })),
  };
}

type EnvMap = Map<string, { host: EnvTimelineHost; workspaces: Map<string, Map<string, EnvTimelineEnvironment>> }>;

function hostKey(host: EnvTimelineHost): string {
  return host.host_id ?? '';
}

function index(view: EnvTimelineView): EnvMap {
  const out: EnvMap = new Map();
  for (const host of view.hosts) {
    const entry = out.get(hostKey(host)) ?? { host, workspaces: new Map() };
    out.set(hostKey(host), entry);
    for (const ws of host.workspaces) {
      const envs = entry.workspaces.get(ws.workspace_id) ?? new Map<string, EnvTimelineEnvironment>();
      entry.workspaces.set(ws.workspace_id, envs);
      for (const env of ws.environments) envs.set(env.environment_id, env);
    }
  }
  return out;
}

/** The view again from its index — an environment a merge left with no step drops off, as the hub omits one. */
function assemble(map: EnvMap, base: Omit<EnvTimelineView, 'hosts'>): EnvTimelineView {
  return {
    ...base,
    hosts: [...map.values()]
      .map(({ host, workspaces }) => ({
        ...host,
        workspaces: [...workspaces.entries()]
          .map(([workspace_id, envs]) => ({
            workspace_id,
            environments: [...envs.values()].filter((env) => env.segments.length > 0),
          }))
          .filter((ws) => ws.environments.length > 0),
      }))
      .filter((host) => host.workspaces.length > 0),
  };
}

/**
 * Lay a delta read over the view it catches up. The delta, read from `delta.since` on, carries whole every step that
 * touches that instant or later; so of `previous`, only the steps that ended by then stand, and the delta's take
 * the rest of each environment's track.
 */
export function mergeWindow(previous: EnvTimelineView, delta: EnvTimelineView): EnvTimelineView {
  const cut = Date.parse(delta.since);
  const merged = index(previous);
  for (const entry of merged.values())
    for (const envs of entry.workspaces.values())
      for (const [id, env] of envs)
        envs.set(id, {
          ...env,
          segments: env.segments.filter((s) => s.ended_at !== null && Date.parse(s.ended_at) <= cut),
        });
  for (const [key, fresh] of index(delta)) {
    const entry = merged.get(key) ?? { host: fresh.host, workspaces: new Map() };
    entry.host = fresh.host;
    merged.set(key, entry);
    for (const [wsId, freshEnvs] of fresh.workspaces) {
      const envs = entry.workspaces.get(wsId) ?? new Map<string, EnvTimelineEnvironment>();
      entry.workspaces.set(wsId, envs);
      for (const [id, env] of freshEnvs)
        envs.set(id, {
          environment_id: id,
          segments: [...(envs.get(id)?.segments ?? []), ...env.segments].sort(
            (a, b) => Date.parse(a.started_at) - Date.parse(b.started_at),
          ),
        });
    }
  }
  return assemble(merged, { since: previous.since, until: delta.until, generated_at: delta.generated_at });
}

/** Lay a delta of each environment's newest step over the last one: an environment the delta names moved on. */
export function mergeLatest(previous: EnvTimelineView, delta: EnvTimelineView): EnvTimelineView {
  const merged = index(previous);
  for (const [key, fresh] of index(delta)) {
    const entry = merged.get(key) ?? { host: fresh.host, workspaces: new Map() };
    entry.host = fresh.host;
    merged.set(key, entry);
    for (const [wsId, freshEnvs] of fresh.workspaces) {
      const envs = entry.workspaces.get(wsId) ?? new Map<string, EnvTimelineEnvironment>();
      entry.workspaces.set(wsId, envs);
      for (const [id, env] of freshEnvs) if (env.segments.length > 0) envs.set(id, env);
    }
  }
  return assemble(merged, { since: previous.since, until: delta.until, generated_at: delta.generated_at });
}

/** How far before its last answer a live read catches up from — slack for a fact recorded a little after its instant. */
const CATCH_UP_OVERLAP_MS = 2 * 60_000;
/** How often a live read starts over whole instead of catching up, so nothing a catch-up missed lingers. */
export const FULL_READ_EVERY_MS = 10 * 60_000;

/** A live read's data, with the browser instant its last whole read landed. */
export interface LiveTimeline extends EnvTimelineView {
  readonly readWholeAt: number;
}

/**
 * A live read that catches up: the first read is whole, and each read after asks only for what moved since the last
 * answer, laid over it by `merge` — so a poll or an SSE nudge costs a few kilobytes, not the window again.
 */
export async function catchUp(
  previous: LiveTimeline | undefined,
  nowMs: number,
  whole: () => Promise<EnvTimelineView>,
  since: (fromIso: string) => Promise<EnvTimelineView>,
  merge: (previous: EnvTimelineView, delta: EnvTimelineView) => EnvTimelineView,
): Promise<LiveTimeline> {
  if (previous === undefined || nowMs - previous.readWholeAt > FULL_READ_EVERY_MS)
    return { ...(await whole()), readWholeAt: nowMs };
  const from = new Date(Date.parse(previous.generated_at) - CATCH_UP_OVERLAP_MS).toISOString();
  return { ...merge(previous, await since(from)), readWholeAt: previous.readWholeAt };
}
