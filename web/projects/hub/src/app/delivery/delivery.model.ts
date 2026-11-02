import {
  ageMs,
  type AsyncStateQuery,
  asyncStateOf,
  bounceReason,
  type ChunkStatus,
  chunkLane,
  type ChunkSummary,
  compactRef,
  formatAbsolute,
  formatLocalClockWithDay,
  formatWhen,
  type FlowFactView,
  type KitAsyncStateValue,
  type FlowView,
  parseBounceEnvelope,
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
  runnerBoardLabel,
} from 'fleet';

import { withinDoneWindow } from '../core/done-window';
import { ordinal } from '../core/flow-place.model';
import { graphStepLabel, STEP_RUNNER_SEP } from '../core/step-label';

/** How long a bounce keeps a chunk with no other delivery footprint in the list. */
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
  /** The tenant's repositories, which name each flow's repository. */
  readonly repositories: readonly RepositorySummary[];
  readonly chunks: readonly ChunkSummary[];
  /** Each flow's newest record facts, newest first, by flow id. */
  readonly facts: ReadonlyMap<string, readonly FlowFactView[]>;
  readonly workItems: ReadonlyMap<string, WorkItemsView>;
  readonly projects: readonly { readonly project_id: string; readonly color: string }[];
  /** Each runner's host name by runner id. */
  readonly runnerHosts: ReadonlyMap<string, string>;
  /** The clock the list is judged against — a coarse one, since only the bounce window reads it. */
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

/** The chunk's latest recorded bounce, as a row names it. */
export interface DeliveryBounce {
  /** `failure`, `conflict`, `poll-timeout` — the answer that bounced it. */
  readonly cause: string;
  /** Whether the bounce's turn withdrew its submission from its flows, as the hub records it. */
  readonly withdrawn: boolean;
  /** The envelope's own account, or `null` when it gives none. */
  readonly reason: string | null;
  /** How many bounces the chunk has had. */
  readonly count: number;
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
  /** When it landed, else its latest flow fact; `null` when no instant is known. The view draws it with
   * {@link deliveryWhen} against the live clock. */
  readonly at: string | null;
  /** The furthest stage reached, an index into {@link STAGES}; `-1` for none. */
  readonly stage: number;
  /** The chunk's latest recorded bounce while it stands out of line ({@link chunkBounce}), else `null`. */
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
 * merge, a fact on a flow, or a recorded bounce — and every running chunk, the next to reach a flow. */
export function deliveryChunkIds(chunks: readonly ChunkSummary[], facts: DeliveryInputs['facts']): readonly string[] {
  const onFlow = new Set([...facts.values()].flat().map((fact) => fact.chunk_id));
  return chunks
    .filter(
      (chunk) =>
        chunk.landed ||
        chunk.open_prs.length > 0 ||
        chunk.flow_places.length > 0 ||
        chunk.awaiting_external_merge ||
        onFlow.has(chunk.chunk_id) ||
        chunk.latest_bounce !== null ||
        chunk.status === 'running',
    )
    .map((chunk) => chunk.chunk_id);
}

function chunkRef(chunk: ChunkSummary, inputs: DeliveryInputs): DeliveryChunkRef {
  const view = inputs.workItems.get(chunk.chunk_id);
  const color = inputs.projects.find((project) => project.project_id === chunk.project_id)?.color;
  return {
    chunkId: chunk.chunk_id,
    shortId: compactRef(chunk.chunk_id),
    lines: workLines(chunk.work_refs, view),
    titlesPending: view === undefined,
    tint: projectTint(color),
    projectName: chunk.project_name,
  };
}

/**
 * A chunk's current bounce, from the hub's record of it: its latest bounce, while the chunk holds no place on any flow
 * and has not landed — a place means it was resubmitted after the bounce and is back in line, so the bounce is
 * history. A chunk with no other delivery footprint keeps it only for {@link BOUNCE_WINDOW_MS}.
 */
function chunkBounce(chunk: ChunkSummary, inputs: DeliveryInputs): DeliveryBounce | null {
  const latest = chunk.latest_bounce;
  if (latest === null || chunk.landed || chunk.flow_places.length > 0) return null;
  const footprint = chunk.open_prs.length > 0 || chunk.awaiting_external_merge;
  const age = ageMs(latest.recorded_at, inputs.now);
  if (!footprint && (age === null || age >= BOUNCE_WINDOW_MS)) return null;
  const reason = parseBounceEnvelope(latest.envelope)?.detail ? bounceReason(latest.envelope) : null;
  return {
    cause: latest.cause,
    withdrawn: latest.withdrawn,
    reason,
    count: chunk.bounce_count,
    at: latest.recorded_at,
    title: [reason, formatAbsolute(latest.recorded_at)].filter((part) => part !== null).join('\n'),
  };
}

/** The chunk's latest turn that judged its submission, on any flow, or `undefined` for none. */
function latestVerdict(chunk: ChunkSummary, inputs: DeliveryInputs): FlowFactView | undefined {
  return chunkFacts(chunk.chunk_id, inputs.facts).find((fact) => fact.verdict !== null);
}

/** The furthest pipeline stage a chunk has reached across its repositories — a turn that landed with nothing to merge
 * counts as landed, though it leaves no landed repository behind. */
function chunkStage(chunk: ChunkSummary, inputs: DeliveryInputs): number {
  const places = chunk.flow_places;
  if (chunk.landed_repos.length > 0) return 3;
  if (latestVerdict(chunk, inputs)?.verdict === 'landed') return 3;
  if (places.some((place) => place.state === 'landing')) return 2;
  if (places.length > 0) return 1;
  if (chunk.open_prs.length > 0 || chunk.awaiting_external_merge) return 0;
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
  const places = chunk.flow_places
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
  const step = node ? graphStepLabel(chunk.graph_name, node) : null;
  const runner = chunk.runner_id ? runnerBoardLabel(runnerBoardParts(chunk.runner_id, chunk.runner_name, inputs.runnerHosts.get(chunk.runner_id))) : null;
  return [step, runner].filter((part) => part !== null).join(STEP_RUNNER_SEP) || null;
}

function inFlightRow(chunk: ChunkSummary, inputs: DeliveryInputs): DeliveryRow | null {
  const bounce = chunkBounce(chunk, inputs);
  const stage = chunkStage(chunk, inputs);
  if (stage < 0 && !bounce) return null;
  return {
    chunk: chunkRef(chunk, inputs),
    at: chunkFacts(chunk.chunk_id, inputs.facts)[0]?.at ?? null,
    stage,
    bounce,
    line: chunkLine(chunk, inputs),
    step: stepLine(chunk, inputs),
    links: chunk.open_prs.map(prLink),
    icon: inFlightIcon(chunk),
    statusColor: toneColor(bounce ? 'stale' : STATUS_TONE(chunk.status)),
  };
}

function landedRow(chunk: ChunkSummary, inputs: DeliveryInputs): DeliveryRow {
  const facts = chunkFacts(chunk.chunk_id, inputs.facts);
  const landedAt = (facts.find((fact) => fact.kind === 'landed') ?? facts.find((fact) => fact.verdict === 'landed'))?.at;
  const commits = chunk.landed_repos.map((landed) => ({
    repo: repoName(landed.repo),
    text: shortCommitHash(landed.commit_hash),
    url: landed.url,
    title: `${landed.repo} ${landed.commit_hash}`,
  }));
  return {
    chunk: chunkRef(chunk, inputs),
    at: landedAt ?? chunk.completed_at ?? null,
    stage: STAGES.length - 1,
    bounce: null,
    line: null,
    step: stepLine(chunk, inputs),
    links: [...commits, ...chunk.closed_prs.map(prLink)],
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

/**
 * The list's read state: loading until the flows, the chunks, and the first of the flows' records are read — a flow
 * whose record is read later fills in where it stands rather than returning the list to its skeleton — then the
 * reads' error, else empty for a list with no row.
 */
export function deliveryState(
  queries: readonly AsyncStateQuery[],
  facts: { readonly facts: ReadonlyMap<string, unknown>; readonly pending: boolean },
  list: DeliveryList,
): KitAsyncStateValue {
  if (facts.pending && facts.facts.size === 0) return 'loading';
  return asyncStateOf(queries, list.delivering.length + list.delivered.length === 0);
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

/** The delivered rows a phone lists: those delivered within the board's own done window, rolling with the clock. */
export function deliveredRecently(list: DeliveryList, now: number): readonly DeliveryRow[] {
  return list.delivered.filter((row) => withinDoneWindow(row.at, now));
}

/** The stage a phone's card names: a bounce, its place in line, or the furthest stage reached. */
export function cardStageLabel(row: DeliveryRow): string {
  if (row.bounce) return `${row.bounce.cause}${row.bounce.withdrawn ? ' · withdrawn' : ''}`;
  if (row.stage === 1 && row.line) return `in line ${row.line.best}`;
  return row.stage >= 0 ? STAGES[row.stage] : 'no stage';
}

/** The colour a phone's card names its stage in: red for a bounce, done's for a landing, cyan for a stage reached. */
export function cardStageColor(row: DeliveryRow): string {
  if (row.bounce) return 'var(--red)';
  if (row.stage === STAGES.length - 1) return toneColor('done');
  return row.stage >= 0 ? 'var(--cyan)' : 'var(--label)';
}
