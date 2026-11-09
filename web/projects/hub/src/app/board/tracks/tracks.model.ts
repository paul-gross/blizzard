import {
  type AsyncStateQuery,
  type ChunkSummary,
  type KitAsyncStateValue,
  type ProjectView,
  asyncState,
  compactRef,
  formatHeldFor,
  projectTint,
} from 'fleet';

import type { NodeBlock } from '../node-strip/node-strip.model';
import type { EnvTimelineHost, EnvTimelineView } from './env-timeline.model';
import { RUNNER_HUES, type TrackItem, envBlocks, stepTooltip } from './tracks-blocks.model';
import type { ResolvedTrackWindow } from './tracks-window.model';
import { type EnvTimelineSegment, type StepKey, type StepRef, hostLabel, segmentCost } from './step.model';

/** A row of the tracks: a host's header row, or one environment's row nested under it. */
export interface TrackGroup {
  readonly id: string;
  readonly kind: 'host' | 'workspace' | 'env';
  readonly label: string;
  /** Under a host: its runners' names. Otherwise empty. */
  readonly detail: string;
  /** A host's workspace row ids, or a workspace's environment row ids, in display order. */
  readonly nestedGroups?: readonly string[];
  readonly order: number;
}

/** The present column's line for one row. */
export type PresentRow =
  | {
      readonly kind: 'host';
      readonly groupId: string;
      readonly label: string;
      readonly detail: string;
    }
  | {
      readonly kind: 'env';
      readonly groupId: string;
      readonly status: 'running' | 'halted';
      /** The escalation that halted it, when halted. */
      readonly escalation: string | null;
      readonly chunkId: string;
      readonly ref: string;
      readonly node: string;
      /** Where it runs: the runner, under its host. */
      readonly runner: string;
      /** The runner's hue class (`tk-rN`), when blocks are coloured by runner. */
      readonly runnerHue: string | null;
      readonly inStep: string;
      /** The step's spend so far; empty while none is recorded. */
      readonly cost: string;
      /** Everything about the step on one line each, for the hover. */
      readonly detail: string;
    }
  | { readonly kind: 'workspace'; readonly groupId: string }
  | { readonly kind: 'idle'; readonly groupId: string; readonly since: string };

export interface Tracks {
  readonly groups: readonly TrackGroup[];
  readonly items: readonly TrackItem[];
  /** Each runner's hue class, by name — `null` when blocks wear their project's colour instead. */
  readonly runnerHues: ReadonlyMap<string, string> | null;
}

interface EnvRow {
  readonly groupId: string;
  readonly host: EnvTimelineHost;
  readonly label: string;
  readonly segments: readonly EnvTimelineSegment[];
}

interface WorkspaceRows {
  readonly groupId: string;
  readonly label: string;
  readonly envs: readonly EnvRow[];
}

interface HostRows {
  readonly groupId: string;
  readonly host: EnvTimelineHost;
  readonly workspaces: readonly WorkspaceRows[];
}

const byNatural = new Intl.Collator(undefined, { numeric: true }).compare;

function hostKey(host: EnvTimelineHost): string {
  return host.host_id ?? 'none';
}

function envGroupId(host: EnvTimelineHost, workspaceId: string, environmentId: string): string {
  return `e:${hostKey(host)}:${workspaceId}/${environmentId}`;
}

/** The newest instant a workspace's steps touch, so the pool in use now sorts above the ones it replaced. */
function lastActive(envs: readonly EnvRow[]): number {
  let latest = 0;
  for (const env of envs) for (const s of env.segments) latest = Math.max(latest, Date.parse(s.ended_at ?? '9999-12-31T00:00:00Z'));
  return latest;
}

/**
 * The rows: every host, its workspaces most-recently-active first, and each workspace's environments in natural order.
 * An environment has a row while it has a step in the window or holds a chunk now — `live` adds the busy ones the
 * window does not reach, so the present column always has a row for what is happening now.
 */
function hostRows(view: EnvTimelineView, live: EnvTimelineView | undefined): readonly HostRows[] {
  const hosts = new Map<string, { host: EnvTimelineHost; workspaces: Map<string, Map<string, EnvRow>> }>();
  const add = (source: EnvTimelineView, withSegments: boolean): void => {
    for (const host of source.hosts) {
      for (const ws of host.workspaces) {
        for (const env of ws.environments) {
          // From the live read, only an environment holding a chunk right now earns a row; one that went idle
          // before the window opened stays off it, so a retired pool drops away once the window moves past it.
          if (!withSegments && env.segments.at(-1)?.ended_at !== null) continue;
          const key = hostKey(host);
          const entry = hosts.get(key) ?? { host, workspaces: new Map<string, Map<string, EnvRow>>() };
          hosts.set(key, entry);
          const envs = entry.workspaces.get(ws.workspace_id) ?? new Map<string, EnvRow>();
          entry.workspaces.set(ws.workspace_id, envs);
          if (envs.has(env.environment_id)) continue;
          envs.set(env.environment_id, {
            groupId: envGroupId(host, ws.workspace_id, env.environment_id),
            host: entry.host,
            label: env.environment_id,
            segments: withSegments ? env.segments : [],
          });
        }
      }
    }
  };
  add(view, true);
  if (live !== undefined) add(live, false);
  const liveLast = live === undefined ? new Map<string, number>() : lastActivity(live);
  return [...hosts.values()].map(({ host, workspaces }) => ({
    groupId: `h:${hostKey(host)}`,
    host,
    workspaces: [...workspaces.entries()]
      .map(([workspaceId, envs]) => {
        const rows = [...envs.values()].sort((a, b) => byNatural(a.label, b.label));
        const active = Math.max(lastActive(rows), ...rows.map((r) => liveLast.get(r.groupId) ?? 0));
        return { groupId: `w:${hostKey(host)}:${workspaceId}`, label: workspaceId, envs: rows, active };
      })
      .sort((a, b) => b.active - a.active || byNatural(a.label, b.label)),
  }));
}

/** Each environment's newest step in `live`, by row id. */
function liveSteps(live: EnvTimelineView | undefined): Map<string, EnvTimelineSegment> {
  const steps = new Map<string, EnvTimelineSegment>();
  for (const host of live?.hosts ?? [])
    for (const ws of host.workspaces)
      for (const env of ws.environments) {
        const last = env.segments.at(-1);
        if (last !== undefined) steps.set(envGroupId(host, ws.workspace_id, env.environment_id), last);
      }
  return steps;
}

function lastActivity(live: EnvTimelineView): Map<string, number> {
  const out = new Map<string, number>();
  for (const [id, s] of liveSteps(live)) out.set(id, Date.parse(s.ended_at ?? '9999-12-31T00:00:00Z'));
  return out;
}

/**
 * The tracks' rows and blocks: a header row per host and per workspace, one row per environment, one block per node
 * step — or, for steps narrower than `minBlockMs`, per fold of them (see {@link envBlocks}). Blocks take their hue
 * from their project's colour when `projectHues` is given (every project in view), else from their runner's.
 */
export function buildTracks(
  view: EnvTimelineView,
  live: EnvTimelineView | undefined,
  nowMs: number,
  projectHues: ReadonlyMap<string, string> | null = null,
  selected: StepKey | null = null,
  minBlockMs = 0,
): Tracks {
  const groups: TrackGroup[] = [];
  const items: TrackItem[] = [];
  // Each runner keeps one hue across every row, in name order so a reload never repaints it.
  const runnerNames = new Set<string>();
  for (const source of [view, live])
    for (const host of source?.hosts ?? []) {
      for (const name of host.runner_names) runnerNames.add(name);
      for (const ws of host.workspaces) for (const env of ws.environments) for (const s of env.segments) runnerNames.add(s.runner_name);
    }
  const runnerHue =
    projectHues === null ? new Map([...runnerNames].sort(byNatural).map((name, i) => [name, i % RUNNER_HUES] as const)) : null;
  for (const host of hostRows(view, live)) {
    groups.push({
      id: host.groupId,
      kind: 'host',
      label: hostLabel(host.host),
      detail: host.host.runner_names.join(', '),
      nestedGroups: host.workspaces.map((ws) => ws.groupId),
      order: groups.length,
    });
    for (const ws of host.workspaces) {
      groups.push({
        id: ws.groupId,
        kind: 'workspace',
        label: ws.label,
        detail: '',
        nestedGroups: ws.envs.map((env) => env.groupId),
        order: groups.length,
      });
    for (const env of ws.envs) {
      groups.push({
        id: env.groupId,
        kind: 'env',
        label: env.label,
        detail: '',
        order: groups.length,
      });
      items.push(...envBlocks(env, nowMs, minBlockMs, { runnerHue, projectHues }, selected));
    }
    }
  }
  return {
    groups,
    items,
    runnerHues: runnerHue === null ? null : new Map([...runnerHue].map(([name, hue]) => [name, `tk-r${hue}`])),
  };
}

/**
 * The present column, one line per row in {@link buildTracks}'s order. Read from `live` alone — what each environment
 * holds now, or how long it has sat idle — so it never depends on the window on screen.
 */
export function presentRows(
  view: EnvTimelineView,
  live: EnvTimelineView | undefined,
  nowMs: number,
  runnerHues: ReadonlyMap<string, string> | null = null,
): readonly PresentRow[] {
  const now = liveSteps(live);
  return hostRows(view, live).flatMap((host): PresentRow[] => [
    {
      kind: 'host',
      groupId: host.groupId,
      label: hostLabel(host.host),
      detail: host.host.runner_names.join(', '),
    },
    ...host.workspaces.flatMap((ws): PresentRow[] => [
      { kind: 'workspace', groupId: ws.groupId },
      ...ws.envs.map((env): PresentRow => {
        const last = now.get(env.groupId);
        if (last === undefined) return { kind: 'idle', groupId: env.groupId, since: live === undefined ? '' : '14d+' };
        if (last.ended_at !== null)
          return { kind: 'idle', groupId: env.groupId, since: formatHeldFor(nowMs - Date.parse(last.ended_at)) };
        return {
          kind: 'env',
          groupId: env.groupId,
          status: last.escalation_open ? 'halted' : 'running',
          escalation: last.escalation_open ? last.escalation : null,
          chunkId: last.chunk_id,
          ref: compactRef(last.chunk_id),
          node: last.node_name,
          runner: `${hostLabel(env.host)}/${last.runner_name}`,
          runnerHue: runnerHues?.get(last.runner_name) ?? null,
          inStep: formatHeldFor(nowMs - Date.parse(last.started_at)),
          cost: last.cost_usd === 0 ? '' : segmentCost(last),
          detail: stepTooltip(last, env.host, nowMs),
        };
      }),
    ]),
  ]);
}

/** Whether the window on screen lies inside the live read — always, while following now. */
export function windowCovered(window: ResolvedTrackWindow, live: EnvTimelineView | undefined): boolean {
  return window.live || (live !== undefined && window.startMs >= Date.parse(live.since));
}

/** The data the window draws from: the live read while it covers the window, else the pinned range's read. */
export function windowData(
  covered: boolean,
  live: EnvTimelineView | undefined,
  range: EnvTimelineView | undefined,
): EnvTimelineView | undefined {
  return covered ? live : range;
}

/** The window's read state: the read it draws from, empty while neither it nor the present read holds a host. */
export function tracksState(
  covered: boolean,
  liveQuery: AsyncStateQuery,
  rangeQuery: AsyncStateQuery,
  data: EnvTimelineView,
  present: EnvTimelineView | undefined,
): KitAsyncStateValue {
  return asyncState(covered ? liveQuery : rangeQuery, data.hosts.length === 0 && (present?.hosts.length ?? 0) === 0);
}

/** Each chunk's progress through its graph, by chunk id. */
export function progressByChunk(chunks: readonly ChunkSummary[] | undefined): ReadonlyMap<string, readonly NodeBlock[]> {
  return new Map((chunks ?? []).map((c) => [c.chunk_id, c.graph_progress]));
}

/** Each project's tint, by id, while blocks wear their project's colour — else `null`, and they wear their runner's. */
export function projectHuesOf(
  showProjectColors: boolean,
  projects: readonly ProjectView[] | undefined,
): ReadonlyMap<string, string> | null {
  if (!showProjectColors) return null;
  const hues = new Map<string, string>();
  for (const p of projects ?? []) {
    const tint = projectTint(p.color);
    if (tint !== null) hues.set(p.project_id, tint);
  }
  return hues;
}

/**
 * The step to highlight for the step `ref` names: its chunk, at the step `read` resolved it to — the whole chunk alone
 * (an empty `startedAt`) until that read has landed on the same chunk.
 */
export function highlightedStep(ref: StepRef | null, read: EnvTimelineSegment | undefined): StepKey | null {
  if (ref === null) return null;
  return { chunkId: ref.chunkId, startedAt: read?.chunk_id === ref.chunkId ? read.started_at : '' };
}

/**
 * Steps narrower than this fold together: `minPx` pixels at `msPerPx`, bucketed to powers of two so a zoom refolds the
 * blocks only when it doubles or halves the scale, not on every wheel tick. `0` before the scale is known.
 */
export function foldBelowMs(msPerPx: number, minPx: number): number {
  const ms = msPerPx * minPx;
  return ms <= 0 ? 0 : 2 ** Math.ceil(Math.log2(ms));
}
