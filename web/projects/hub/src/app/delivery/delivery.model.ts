import {
  ageMs,
  type ChunkStatus,
  chunkLane,
  type ChunkSummary,
  compactRef,
  formatAbsolute,
  formatLocalClockWithDay,
  formatSeenTag,
  formatWhen,
  type hubApi,
  type FlowFactView,
  type FlowView,
  type PrView,
  type RepositorySummary,
  projectTint,
  runnerBoardParts,
  shortCommitHash,
  STATUS_TONE,
  toneColor,
  type WorkItemsView,
  type WorkLine,
  workLines,
} from 'fleet';

import { ordinal } from '../board/flows/flow-place.model';
import { graphStepLabel, runnerBoardLabel, STEP_RUNNER_SEP } from '../board/node-strip/node-strip.model';
import { bounceReason, parseBounceEnvelope } from './delivery-bounce.model';

/** How far back a turn that bounced still counts as delivery activity. */
const BOUNCE_WINDOW_MS = 24 * 60 * 60_000;

/** The delivery pipeline's stages, in order. */
export const STAGES = ['PR open', 'in line', 'landing', 'landed'] as const;

/** A chunk as every Delivery row names it: its work refs over their titles, tinted by its project. */
export interface DeliveryChunkRef {
  readonly chunkId: string;
  readonly shortId: string;
  readonly lines: readonly WorkLine[];
  /** Whether the titles are still being read, so a row holds a skeleton in their place. */
  readonly titlesPending: boolean;
  readonly tint: string | null;
  readonly projectName: string | null;
}

/** Everything the page reads, handed whole to the joins below. */
export interface DeliveryInputs {
  readonly flows: readonly FlowView[];
  /** Each in-flight chunk's own bounce record, by chunk id — absent while its detail is still unread. */
  readonly bounces: ReadonlyMap<string, readonly hubApi.BounceView[]>;
  /** The tenant's repositories, which name each flow's repository. */
  readonly repositories: readonly RepositorySummary[];
  readonly chunks: readonly ChunkSummary[];
  /** Each flow's newest record facts, newest first, by flow id. */
  readonly facts: ReadonlyMap<string, readonly FlowFactView[]>;
  readonly workItems: ReadonlyMap<string, WorkItemsView>;
  readonly projects: readonly { readonly project_id: string; readonly color: string }[];
  readonly graphNames: ReadonlyMap<string, string>;
  /** Each runner's host name by runner id. */
  readonly runnerHosts: ReadonlyMap<string, string>;
  readonly now: number;
}

/** A row's date: the time, with the day above it for an instant before today. */
export interface DeliveryWhen {
  /** `Yesterday`, the date (`2026/10/30`), or `null` for today. */
  readonly day: string | null;
  /** `HH:MM`. */
  readonly time: string;
  /** The absolute instant, for the hover. */
  readonly title: string;
}

/** An instant as the date column draws it — the board's own short stamps, split over two lines: the day (as
 * `formatWhen` names an older one) over the `HH:MM` it names a same-day one by. */
export function deliveryWhen(iso: string | null, now: number): DeliveryWhen | null {
  const clock = formatLocalClockWithDay(iso, new Date(now));
  if (iso === null || clock === null) return null;
  const day = clock.day === null ? null : clock.day === 'Yesterday' ? clock.day : formatWhen(iso, new Date(now));
  return { day, time: formatWhen(iso, new Date(iso)), title: formatAbsolute(iso) };
}

/** A PR or commit link in a row's links column, its repository named dimly before it. */
export interface DeliveryLink {
  /** The repository's short name: `blizzard`. */
  readonly repo: string;
  /** `#1795` or a short commit hash. */
  readonly text: string;
  readonly url: string | null;
  readonly title: string;
}

/** What a row's lane icon draws: a lane key and the status it is colored by. */
export interface DeliveryIcon {
  readonly lane: string;
  readonly status: ChunkStatus;
}

/** The lanes that wait on a human — an in-flight chunk there keeps its own icon. */
const HUMAN_LANES: ReadonlySet<string> = new Set(['needs', 'waiting']);

/** An in-flight chunk's icon: its own lane where that waits on a human, else the running lane's spinner. */
function inFlightIcon(chunk: ChunkSummary): DeliveryIcon {
  const lane = chunkLane(chunk);
  return HUMAN_LANES.has(lane) ? { lane, status: chunk.status } : { lane: 'running', status: 'running' };
}

/** A turn that ended without landing. */
export interface DeliveryBounce {
  /** `failure`, `conflict`, `poll-timeout`. */
  readonly cause: string;
  /** The envelope's own account — its `detail`, else the failed step's exit — or `null` for none. */
  readonly reason: string | null;
  /** How many bounces the chunk has had; `1` while only the flow record has been read. */
  readonly count: number;
  /** How long ago, bracketed as the fleet registry tags an age: `[50m]`. */
  readonly age: string;
  /** The full reason and the absolute instant, for the hover. */
  readonly title: string;
  /** When it happened. */
  readonly at: string;
}

/** A chunk's places in line: its best position, and every flow's position named by repository. */
export interface DeliveryLine {
  /** `#2` — the lowest position the chunk holds on any flow. */
  readonly best: string;
  /** `2nd on blizzard · 1st on blizzard-context` — only when it is in line on more than one flow. */
  readonly perRepo: string | null;
}

/** One chunk's row in the Delivery list. */
export interface DeliveryRow {
  readonly chunk: DeliveryChunkRef;
  /** When it landed, else its latest flow fact; `null` when no instant is known. */
  readonly at: string | null;
  /** {@link at} as the date column draws it, or `null`. */
  readonly when: DeliveryWhen | null;
  /** The furthest stage reached, an index into {@link STAGES}; `-1` for none. */
  readonly stage: number;
  /** The chunk's latest turn's bounce — its outcome and when — or `null` when its latest turn did not bounce. */
  readonly bounce: DeliveryBounce | null;
  /** Its places in line, or `null` when it holds none. */
  readonly line: DeliveryLine | null;
  /** `graph/node · host/runner` — the runner part only while one holds the chunk. */
  readonly step: string | null;
  /** In flight: its open PRs. Landed: its commits, then its merged PRs. */
  readonly links: readonly DeliveryLink[];
  /** The lane and status the row's status icon draws. */
  readonly icon: DeliveryIcon;
  /** The row's left-edge color: the failure tone for a bounce, else its status's tone, done's for a landed chunk. */
  readonly statusColor: string;
}

/** The ids of every flow read. */
export function flowIds(flows: readonly FlowView[]): readonly string[] {
  return flows.map((flow) => flow.flow_id);
}

/** The chunk ids whose titles the page reads: every chunk that can hold a row — any delivery footprint, an awaited
 * merge, or a fact on a flow, where a bounce is read from — and every running chunk, the next to reach a flow. */
export function deliveryChunkIds(chunks: readonly ChunkSummary[], facts: DeliveryInputs['facts']): readonly string[] {
  const onFlow = new Set([...facts.values()].flat().map((fact) => fact.chunk_id));
  return chunks
    .filter(
      (chunk) =>
        chunk.landed ||
        (chunk.open_prs ?? []).length > 0 ||
        (chunk.flow_places ?? []).length > 0 ||
        chunk.awaiting_external_merge ||
        onFlow.has(chunk.chunk_id) ||
        chunk.status === 'running',
    )
    .map((chunk) => chunk.chunk_id);
}

/** The chunks whose own detail the page reads for their bounce record: every unlanded chunk with a delivery
 * footprint. */
export function deliveryDetailIds(chunks: readonly ChunkSummary[]): readonly string[] {
  return chunks
    .filter((chunk) => !chunk.landed && ((chunk.open_prs ?? []).length > 0 || (chunk.flow_places ?? []).length > 0 || chunk.awaiting_external_merge))
    .map((chunk) => chunk.chunk_id);
}

/** The graph ids the rows name a step in. */
export function deliveryGraphIds(chunks: readonly ChunkSummary[]): readonly string[] {
  return chunks.flatMap((chunk) => (chunk.graph_id ? [chunk.graph_id] : []));
}

function chunkRef(chunk: ChunkSummary, inputs: DeliveryInputs): DeliveryChunkRef {
  const view = inputs.workItems.get(chunk.chunk_id);
  const color = inputs.projects.find((project) => project.project_id === chunk.project_id)?.color;
  return {
    chunkId: chunk.chunk_id,
    shortId: compactRef(chunk.chunk_id),
    lines: workLines(chunk.work_refs ?? [], view),
    titlesPending: view === undefined,
    tint: projectTint(color),
    projectName: chunk.project_name ?? null,
  };
}

function isBounce(fact: FlowFactView): boolean {
  return fact.kind === 'turn-ended' && fact.outcome !== null && fact.outcome !== 'pending' && fact.outcome !== 'landed';
}

/** The chunk's latest settled turn on this flow, when it bounced recently and the chunk holds no place there since. */
function bounceOn(flow: FlowView, chunk: ChunkSummary, inputs: DeliveryInputs): FlowFactView | null {
  if ((chunk.flow_places ?? []).some((place) => place.flow_id === flow.flow_id)) return null;
  const latest = (inputs.facts.get(flow.flow_id) ?? []).find((fact) => fact.chunk_id === chunk.chunk_id && fact.kind === 'turn-ended' && fact.outcome !== 'pending');
  if (!latest || !isBounce(latest)) return null;
  const age = ageMs(latest.at, inputs.now);
  return age !== null && age < BOUNCE_WINDOW_MS ? latest : null;
}

/**
 * A chunk's current bounce, from its own bounce record: its latest bounce, while the chunk holds no place on any flow
 * and has not landed. A place means it was resubmitted after the bounce and is back in line, so the bounce is history.
 */
function chunkBounce(chunk: ChunkSummary, bounces: readonly hubApi.BounceView[], now: number): DeliveryBounce | null {
  if (chunk.landed || (chunk.flow_places ?? []).length > 0) return null;
  const latest = [...bounces].sort((a, b) => b.recorded_at.localeCompare(a.recorded_at))[0];
  if (!latest) return null;
  const reason = bounceReason(parseBounceEnvelope(latest.envelope));
  return {
    cause: latest.cause,
    reason,
    count: bounces.length,
    age: formatSeenTag(latest.recorded_at, now),
    at: latest.recorded_at,
    title: [reason, formatAbsolute(latest.recorded_at)].filter((part) => part).join('\n'),
  };
}

/** A chunk's current bounce while its own record is unread: the flow record's latest recent bounce of it. */
function flowBounce(chunk: ChunkSummary, inputs: DeliveryInputs): DeliveryBounce | null {
  const latest = inputs.flows.flatMap((flow) => bounceOn(flow, chunk, inputs) ?? []).sort((a, b) => b.at.localeCompare(a.at))[0];
  if (!latest) return null;
  return { cause: latest.outcome ?? 'bounced', reason: null, count: 1, age: formatSeenTag(latest.at, inputs.now), at: latest.at, title: formatAbsolute(latest.at) };
}

/**
 * The bounce a row shows: the chunk's own latest bounce, with its reason — unless the flow record holds a later turn
 * that bounced (a turn can end in failure with no bounce recorded on the chunk), then that turn's outcome and time,
 * carrying the chunk's bounce count. While the chunk's own record is unread, the flow record's alone.
 */
function currentBounce(own: DeliveryBounce | null, flow: DeliveryBounce | null): DeliveryBounce | null {
  if (own === null) return flow;
  if (flow === null || flow.at <= own.at) return own;
  return { ...flow, count: own.count + 1, title: `${flow.title}\nNo bounce recorded on the chunk for this turn; its last: ${own.cause} ${own.title}` };
}

/** The furthest pipeline stage a chunk has reached across its repositories. */
function chunkStage(chunk: ChunkSummary): number {
  const places = chunk.flow_places ?? [];
  if ((chunk.landed_repos ?? []).length > 0) return 3;
  if (places.some((place) => place.state === 'landing')) return 2;
  if (places.length > 0) return 1;
  if ((chunk.open_prs ?? []).length > 0 || chunk.awaiting_external_merge) return 0;
  return -1;
}

/** The chunk's facts across every flow, newest first. */
function chunkFacts(chunkId: string, facts: DeliveryInputs['facts']): readonly FlowFactView[] {
  return [...facts.values()].flat().filter((fact) => fact.chunk_id === chunkId).sort((a, b) => b.at.localeCompare(a.at));
}

/** The name a flow's repository goes by: the repository's own name, else the place's, else the flow's resource. */
function flowRepoName(flowId: string, fallback: string, inputs: DeliveryInputs): string {
  const flow = inputs.flows.find((each) => each.flow_id === flowId);
  const repository = flow ? inputs.repositories.find((each) => each.repository_id === flow.repository_id) : undefined;
  return repository?.name ?? fallback;
}

/** A chunk's places in line — every place that is neither landing nor parked off the line — or `null` for none. */
export function chunkLine(chunk: ChunkSummary, inputs: DeliveryInputs): DeliveryLine | null {
  const places = (chunk.flow_places ?? [])
    .filter((place) => place.state !== 'landing' && place.state !== 'awaiting-merge' && place.state !== 'passed-over')
    .sort((a, b) => a.position - b.position);
  const best = places[0];
  if (!best) return null;
  return {
    best: `#${best.position}`,
    perRepo:
      places.length > 1
        ? places.map((place) => `${ordinal(place.position)} on ${flowRepoName(place.flow_id, place.repository_name ?? place.repository_slug ?? place.resource, inputs)}`).join(' · ')
        : null,
  };
}

/** The short name of a forge-side `owner/repo`. */
function repoName(forge: string): string {
  return forge.split('/').pop() ?? forge;
}

function prLink(pr: PrView): DeliveryLink {
  return { repo: repoName(pr.repo), text: `#${pr.number}`, url: pr.url, title: `${pr.repo}#${pr.number}` };
}

/** `graph/node · host/runner`, the runner part only while one holds the chunk. */
function stepLine(chunk: ChunkSummary, inputs: DeliveryInputs): string | null {
  const node = chunk.current_node_name ?? chunk.current_node_id;
  const graphName = chunk.graph_id ? inputs.graphNames.get(chunk.graph_id) : null;
  const step = node ? graphStepLabel(graphName, node) : null;
  const runner = chunk.runner_id ? runnerBoardLabel(runnerBoardParts(chunk.runner_id, chunk.runner_name, inputs.runnerHosts.get(chunk.runner_id))) : null;
  return [step, runner].filter((part) => part !== null).join(STEP_RUNNER_SEP) || null;
}

function inFlightRow(chunk: ChunkSummary, inputs: DeliveryInputs): DeliveryRow | null {
  const own = inputs.bounces.get(chunk.chunk_id);
  const bounce = currentBounce(own === undefined ? null : chunkBounce(chunk, own, inputs.now), flowBounce(chunk, inputs));
  const stage = chunkStage(chunk);
  if (stage < 0 && !bounce) return null;
  return {
    chunk: chunkRef(chunk, inputs),
    at: chunkFacts(chunk.chunk_id, inputs.facts)[0]?.at ?? null,
    when: deliveryWhen(chunkFacts(chunk.chunk_id, inputs.facts)[0]?.at ?? null, inputs.now),
    stage,
    bounce,
    line: chunkLine(chunk, inputs),
    step: stepLine(chunk, inputs),
    links: (chunk.open_prs ?? []).map(prLink),
    icon: inFlightIcon(chunk),
    statusColor: toneColor(bounce ? 'stale' : STATUS_TONE(chunk.status)),
  };
}

function landedRow(chunk: ChunkSummary, inputs: DeliveryInputs): DeliveryRow {
  const landedAt = chunkFacts(chunk.chunk_id, inputs.facts).find((fact) => fact.kind === 'landed')?.at;
  const commits = (chunk.landed_repos ?? []).map((landed) => ({
    repo: repoName(landed.repo),
    text: shortCommitHash(landed.commit_hash),
    url: landed.url,
    title: `${landed.repo} ${landed.commit_hash}`,
  }));
  return {
    chunk: chunkRef(chunk, inputs),
    at: landedAt ?? chunk.completed_at ?? null,
    when: deliveryWhen(landedAt ?? chunk.completed_at ?? null, inputs.now),
    stage: STAGES.length - 1,
    bounce: null,
    line: null,
    step: stepLine(chunk, inputs),
    links: [...commits, ...(chunk.closed_prs ?? []).map(prLink)],
    icon: { lane: 'done', status: 'done' },
    statusColor: toneColor(STATUS_TONE('done')),
  };
}

/** The page's list in its two runs: the chunks still delivering, then the chunks delivered. */
export interface DeliveryList {
  readonly delivering: readonly DeliveryRow[];
  readonly delivered: readonly DeliveryRow[];
}

/** Newest first by date; a row with no known instant sorts last. */
function byDate(a: DeliveryRow, b: DeliveryRow): number {
  return (b.at ?? '').localeCompare(a.at ?? '');
}

/**
 * The page's list, a row per chunk with any delivery footprint: the unlanded chunks with an open change, a place on a
 * flow, or a recent bounce, newest latest flow fact first; then every landed chunk, newest landing first.
 */
export function deliveryList(inputs: DeliveryInputs): DeliveryList {
  return {
    delivering: inputs.chunks.flatMap((chunk) => (chunk.landed ? [] : (inFlightRow(chunk, inputs) ?? []))).sort(byDate),
    delivered: inputs.chunks.filter((chunk) => chunk.landed).map((chunk) => landedRow(chunk, inputs)).sort(byDate),
  };
}

/** The listed row for a chunk id, or `null` when the list holds none. */
export function findRow(list: DeliveryList, chunkId: string | null): DeliveryRow | null {
  if (chunkId === null) return null;
  return [...list.delivering, ...list.delivered].find((row) => row.chunk.chunkId === chunkId) ?? null;
}

/** The selection a row's pick leaves: the row, or nothing when it was already selected. */
export function toggledSelection(current: string | null, picked: string): string | null {
  return current === picked ? null : picked;
}
