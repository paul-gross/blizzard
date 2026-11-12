import { type ChunkDetail, type ProjectView, formatWhenParts, projectTint } from 'fleet';

import type { LeaseTranscriptRef } from './lease-transcript.query';
import {
  type EnvTimelineSegment,
  type PlacedStep,
  type StepRef,
  nodeShade,
  refNames,
  stepEndMs,
  stepHeld,
  stepHeldMs,
  stepRefKey,
  stepRefOf,
} from './step.model';
import type { EnvTimelineStepView } from './step.query';

/** The hub's now as the step read last answered it, or `fallback` before it has. */
export function drawerNow(view: EnvTimelineStepView | undefined, fallback: number): number {
  return view ? Date.parse(view.generated_at) : fallback;
}

/**
 * The step `ref` names on the chunk's path — its newest when `ref` names none — off the path alone, so a pick along it
 * reads at once, before the step's own detail has landed. `null` before the read has answered.
 */
export function placedOf(view: EnvTimelineStepView | undefined, ref: StepRef | null): PlacedStep | null {
  if (view === undefined) return null;
  if (ref === null) return view.step;
  return view.journey.find((p) => refNames(ref, p.segment)) ?? view.step;
}

/** One block of the chunk's journey strip: a share of the strip's width, and where the environment changed. */
export interface JourneyBlock {
  /** Unique along the path — the strip tracks its blocks by it. */
  readonly id: string;
  readonly key: StepRef;
  /** How long the step held — its share of the strip, as a flex weight; a step of seconds still keeps a sliver. */
  readonly heldMs: number;
  /** How long the chunk sat between the step before and this one (queueing) — a gap of that weight before it. */
  readonly gapMs: number;
  readonly shade: number;
  readonly failed: boolean;
  readonly current: boolean;
  /** The node the step ran, labelled on the block when it has the room. */
  readonly node: string;
  readonly live: boolean;
  readonly envChanged: boolean;
  readonly title: string;
}

function whereOf(placed: PlacedStep): string {
  return `${placed.workspace_id} / ${placed.environment_id}`;
}

/** The whole path, laid on one strip from the chunk's first step to its last end, gaps (queueing) included — each
 * block weighted by how long it held. Empty before the read has answered. */
export function journeyBlocks(
  view: EnvTimelineStepView | undefined,
  current: PlacedStep | null,
  nowMs: number,
): readonly JourneyBlock[] {
  const journey = view?.journey ?? [];
  if (current === null || journey.length === 0) return [];
  let reached = Date.parse(journey[0].segment.started_at);
  return journey.map((p, i) => {
    const s = p.segment;
    const gapMs = Math.max(0, Date.parse(s.started_at) - reached);
    reached = Math.max(reached, stepEndMs(s, nowMs));
    const where = whereOf(p);
    const prev = journey[i - 1];
    const key = stepRefOf(p);
    return {
      id: stepRefKey(key),
      key,
      heldMs: Math.max(1, stepHeldMs(s, nowMs)),
      gapMs,
      shade: nodeShade(s.node_name),
      failed: s.failed,
      current: s.started_at === current.segment.started_at,
      node: s.node_name,
      live: s.ended_at === null,
      envChanged: prev !== undefined && whereOf(prev) !== where,
      title: `${s.node_name} · ${stepHeld(s, nowMs)}${s.exit_choice ? ` → ${s.exit_choice}` : ''}\n${where} · ${s.runner_name}`,
    };
  });
}

/** The steps before and after one on the chunk's path. */
export interface StepNeighbours {
  readonly previous: PlacedStep | null;
  readonly next: PlacedStep | null;
}

/** The step before and after `current` on the chunk's path, for the arrived-from / left-for line. */
export function neighbours(view: EnvTimelineStepView | undefined, current: PlacedStep | null): StepNeighbours {
  if (view === undefined || current === null) return { previous: null, next: null };
  const i = view.journey.findIndex((p) => p.segment.started_at === current.segment.started_at);
  return { previous: i > 0 ? view.journey[i - 1] : null, next: i >= 0 ? (view.journey[i + 1] ?? null) : null };
}

/** The harness and model the step's session ran on — its spawn's, else its first invocation's. */
export function stepModel(view: EnvTimelineStepView | undefined): string | null {
  const session = view?.invocations.find((u) => u.kind === 'spawn') ?? view?.invocations[0];
  if (session === undefined) return null;
  return [session.harness_id, session.model].filter(Boolean).join(' · ') || null;
}

/** How long the step has held — `so far` while it still runs. */
function heldSoFar(segment: EnvTimelineSegment, nowMs: number): string {
  return stepHeld(segment, nowMs) + (segment.ended_at === null ? ' so far' : '');
}

/** A local instant as the drawer stamps it: `16:32` today, `Yesterday 02:59`, else the date and the time. */
export function localStamp(iso: string, now: Date): string {
  const { day, time } = formatWhenParts(iso, now);
  return day ? `${day} ${time}` : time;
}

/** A fact row: plain text, or a template the fact list stamps out. */
export type StepFact<T> = { readonly label: string; readonly value: string } | { readonly label: string; readonly template: T };

/** The step's facts, in the board's label/value grid; empty before the step is placed. */
export function stepFacts<T>(
  placed: PlacedStep | null,
  nowMs: number,
  templates: { readonly began: T; readonly ended: T; readonly spend: T },
): readonly StepFact<T>[] {
  if (placed === null) return [];
  return [
    { label: 'Where', value: whereOf(placed) },
    { label: 'Began', template: templates.began },
    { label: 'Ended', template: templates.ended },
    { label: 'Held', value: heldSoFar(placed.segment, nowMs) },
    { label: 'Spend', template: templates.spend },
  ];
}

/** The chunk detail read, once it answers for `chunkId` — a read still on the chunk before reads as none. */
export function chunkFor(detail: ChunkDetail | undefined, chunkId: string | null): ChunkDetail | null {
  return detail !== undefined && detail.chunk_id === chunkId ? detail : null;
}

/** The artifact keys the step read names as its own — `undefined` while the drawer still holds the previous step's
 * read, so its artifacts are never shown against this step. */
export function ownArtifactKeys(
  view: { readonly artifact_keys: readonly string[] } | undefined,
  pending: boolean,
): readonly string[] | undefined {
  return pending ? undefined : view?.artifact_keys;
}

/** The chunk with only the artifacts the step read names as its own; `null` until both the chunk and the step's own
 * detail are read. */
export function stepArtifactsOf(chunk: ChunkDetail | null, keys: readonly string[] | undefined): ChunkDetail | null {
  if (chunk === null || keys === undefined) return null;
  const mine = new Set(keys);
  return { ...chunk, artifacts: chunk.artifacts.filter((a) => mine.has(a.key)) };
}

/** The transcript to read for a step — its session's, fenced by its epoch — or `null` for a step the hub ran itself. */
export function transcriptRefOf(segment: EnvTimelineSegment | undefined): LeaseTranscriptRef | null {
  return segment !== undefined && segment.executor !== 'hub' && segment.epoch !== null
    ? { chunkId: segment.chunk_id, nodeId: segment.node_id, epoch: segment.epoch, live: segment.ended_at === null }
    : null;
}

/** How the drawer's header names a step: its session in full, else its node — with why it names no session. */
export interface StepIdentity {
  readonly label: string;
  readonly title: string;
  /** Whether the hub ran the step itself, with no runner and no session. */
  readonly hub: boolean;
}

/** A runner step with no session id is either one whose session start the hub never heard — no fence either — or a
 * session that took the chunk over on a forced entry, fenced and with a transcript, but minted with no id. */
export function stepIdentity(segment: EnvTimelineSegment): StepIdentity {
  const hub = segment.executor === 'hub';
  if (segment.lease_id !== null) return { label: segment.lease_id, title: segment.lease_id, hub };
  return { label: segment.node_name, title: unnamedSessionTitle(segment, hub), hub };
}

function unnamedSessionTitle(segment: EnvTimelineSegment, hub: boolean): string {
  if (hub) return 'no session of its own — a hub-side step';
  if (segment.epoch === null) return "the hub never heard this step's session start";
  return 'a session that took the chunk over on a forced entry';
}

/** A step's chunk's project: its name, and its colour as the board tints with it. */
export interface StepProject {
  readonly name: string;
  readonly color: string | null;
}

export function projectOf(projects: readonly ProjectView[] | undefined, projectId: string | null | undefined): StepProject | null {
  const p = projects?.find((x) => x.project_id === projectId);
  return p ? { name: p.name, color: projectTint(p.color) } : null;
}

/**
 * What a link off the drawer carries, so the chunk page's back link returns to this layout on this step: `from` names it
 * by its session, `fromAt` by the instant it began — never both.
 */
export interface DrawerOrigin extends Readonly<Record<string, string | null>> {
  readonly layout: string | null;
  readonly from: string | null;
  readonly fromAt: string | null;
}

export function drawerOrigin(layout: string | null, placed: PlacedStep | null): DrawerOrigin {
  const ref = placed ? stepRefOf(placed) : null;
  return { layout, from: ref?.session ?? null, fromAt: ref?.at ?? null };
}
