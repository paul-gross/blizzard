import { compactRef, formatCost, formatHeldFor, type hubApi } from 'fleet';

/** One node step on an environment's track. */
export type EnvTimelineSegment = hubApi.EnvTimelineSegmentView;

/** A step placed where it ran — its host, workspace and environment. */
export type PlacedStep = hubApi.EnvTimelinePlacedStepView;

/** Which node step: the chunk and the instant the step began, as the wire gives it. */
export interface StepKey {
  readonly chunkId: string;
  readonly startedAt: string;
}

/**
 * A node step as the URL and the step read name it: its chunk, and either the session that ran it (`session`) or, for a
 * step no session of its own ran, the instant it began (`at`) — never both; neither names the chunk's newest step.
 */
export interface StepRef {
  readonly chunkId: string;
  readonly session: string | null;
  readonly at: string | null;
}

/** The ref that names a step of its chunk: by its session, else by the instant it began. */
export function stepRefOfSegment(segment: EnvTimelineSegment): StepRef {
  return segment.lease_id === null
    ? { chunkId: segment.chunk_id, session: null, at: segment.started_at }
    : { chunkId: segment.chunk_id, session: segment.lease_id, at: null };
}

/** The ref that names a placed step. */
export function stepRefOf(placed: PlacedStep): StepRef {
  return stepRefOfSegment(placed.segment);
}

/** Whether `ref` names `segment` itself — not merely its chunk's newest step. */
export function refNames(ref: StepRef, segment: EnvTimelineSegment): boolean {
  if (ref.chunkId !== segment.chunk_id) return false;
  if (ref.session !== null) return segment.lease_id === ref.session;
  return ref.at !== null && segment.lease_id === null && segment.started_at === ref.at;
}

/** A stable key for the step `ref` names, unique within its chunk. */
export function stepRefKey(ref: StepRef): string {
  return ref.session ?? ref.at ?? '';
}

/**
 * The step the URL names: the board's selected `?chunk=`, within it `?step=` (a session) or else `?at=` (an instant) —
 * its newest step with neither.
 */
export function stepRefOfParams(params: { get(name: string): string | null }): StepRef | null {
  const chunkId = params.get('chunk');
  if (chunkId === null) return null;
  const session = params.get('step');
  return { chunkId, session, at: session === null ? params.get('at') : null };
}

/** The query params that select `ref` — and, for `null`, clear all three. */
export function stepRefParams(ref: StepRef | null): { chunk: string | null; step: string | null; at: string | null } {
  return { chunk: ref?.chunkId ?? null, step: ref?.session ?? null, at: ref?.at ?? null };
}

/** The node order a delivery graph usually runs in, palest shade first. */
const NODE_SHADE_ORDER = ['plan', 'plan-review', 'build', 'verify', 'review', 'resolve', 'pre-push', 'deliver'];
/** How many shades the node ramp carries (`node-shades.css`'s `tk-n0`…`tk-n7`). */
const SHADES = 8;

/**
 * The shade a node's blocks wear, `0` (palest) to `7` (deepest) — deepening along a delivery graph's usual order, with
 * any node not listed hashed onto the ramp so its shade is at least stable.
 */
export function nodeShade(nodeName: string): number {
  const ordered = NODE_SHADE_ORDER.indexOf(nodeName);
  if (ordered >= 0) return ordered;
  let hash = 0;
  for (const ch of nodeName) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return hash % SHADES;
}

/** When the step ended — `nowMs` while it still runs. */
export function stepEndMs(segment: EnvTimelineSegment, nowMs: number): number {
  return segment.ended_at === null ? nowMs : Date.parse(segment.ended_at);
}

/** How long the step held, to its end or to `nowMs`. */
export function stepHeldMs(segment: EnvTimelineSegment, nowMs: number): number {
  return stepEndMs(segment, nowMs) - Date.parse(segment.started_at);
}

/** How long the step held, as the board writes a duration. */
export function stepHeld(segment: EnvTimelineSegment, nowMs: number): string {
  return formatHeldFor(stepHeldMs(segment, nowMs));
}

/** A spend figure, or `no usage yet` for a running step with none recorded. */
export function costLabel(usd: number, estimated: boolean, running: boolean): string {
  // Usage lands when a harness invocation finishes, so a step still running often has none recorded yet.
  if (usd === 0 && running) return 'no usage yet';
  return estimated ? formatCost(0, usd, false) : formatCost(usd, null, false);
}

/** The step's spend, as {@link costLabel} writes it. */
export function segmentCost(segment: EnvTimelineSegment): string {
  return costLabel(segment.cost_usd, segment.cost_estimated, segment.ended_at === null);
}

/** A runner host's name, its compact id when it has none, or `no host`. */
export function hostLabel(host: { readonly host_id: string | null; readonly host_name: string | null }): string {
  return host.host_id === null ? 'no host' : (host.host_name ?? compactRef(host.host_id));
}
