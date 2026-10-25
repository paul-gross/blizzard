import { DONE_LANE, RunnerConnection, UNKNOWN_LANE, ageMs, chunkLane, compactRef, runnerBoardParts, type RunnerBoardParts, type ChunkStatus, type ChunkSummary, type DecisionView, type QuestionView, type RunnerRegistryView, type WorkItemsView } from 'fleet';

import { workLines } from '../board-card/board-card.model';
import type { RowWork, Vitals } from './glance-view';

/** How far back "Done today" reaches — a rolling window, not a calendar day. */
const DONE_TODAY_WINDOW_MS = 24 * 60 * 60 * 1000;

/** The runners that count toward runners-up: a retired runner has left the fleet, and a never-connected
 * one (added but never started) has not joined it yet — neither reads as down. */
export function liveRunners(runners: readonly RunnerRegistryView[]): readonly RunnerRegistryView[] {
  return runners.filter((runner) => !runner.retired && runner.connection !== RunnerConnection.NEVER_CONNECTED);
}

/** A chunk's work items as its row names them — the board card's own `workLines`, so a glance row and a
 * card agree on what a chunk is. */
function rowWork(chunk: ChunkSummary | undefined, workItems: ReadonlyMap<string, WorkItemsView>): RowWork {
  if (chunk === undefined) return { lines: [], itemsRead: true };
  const view = workItems.get(chunk.chunk_id);
  return { lines: workLines(chunk.work_refs ?? [], view), itemsRead: view !== undefined };
}

/** The vitals strip, counted off the mobile list: what needs a person, what is moving, what is queued, what
 * finished today, and the registry's online fraction. */
export function glanceVitals(runners: readonly RunnerRegistryView[], list: GlanceListGroups): Vitals {
  const online = runners.filter((runner) => runner.online).length;
  return {
    needsYou: list.live.filter((row) => row.lane === 'needs' || row.lane === 'waiting').length,
    running: list.live.filter((row) => row.lane === 'running').length,
    ready: list.ready.length,
    doneToday: list.done.length,
    runnersUpLabel: `${online}/${runners.length}`,
  };
}

/** One card of the mobile list — a chunk read the way the desktop list row reads it. */
export interface GlanceRow extends RowWork {
  readonly chunkId: string;
  readonly shortId: string;
  /** The board lane it sits in — the card's icon. */
  readonly lane: string;
  readonly status: ChunkStatus;
  /** `paused` or `stopped`, which the icon alone cannot tell from its lane on a touch screen; else `null`. */
  readonly statusWord: string | null;
  readonly node: string;
  /** The holding runner's host and name, or `null` when none holds it. */
  readonly runner: RunnerBoardParts | null;
  readonly gated: boolean;
  /** An open ask's question, shown under the card. */
  readonly ask: string | null;
  readonly costUsd: number;
  readonly costPartial: boolean;
  readonly estimatedCostUsd: number | null;
  /** The chunk's project palette color, which tints the card as it does a desktop card; `null` untinted. */
  readonly projectColor: string | null;
}

/** The mobile list in render order: attention and work in flight, then READY over BACKLOG, then done today. */
export interface GlanceListGroups {
  readonly live: readonly GlanceRow[];
  readonly ready: readonly GlanceRow[];
  readonly backlog: readonly GlanceRow[];
  readonly done: readonly GlanceRow[];
}

/** `order`'s order as a rank lookup; an unranked chunk sorts last, keeping its own relative order. */
function byRank<T extends { readonly chunkId: string }>(rows: T[], order: readonly { readonly chunk_id: string }[]): T[] {
  const rank = new Map(order.map((entry, index) => [entry.chunk_id, index]));
  const of = (row: T): number => rank.get(row.chunkId) ?? Number.POSITIVE_INFINITY;
  return rows.sort((a, b) => (of(a) === of(b) ? 0 : of(a) < of(b) ? -1 : 1));
}

/** Every chunk as a mobile list card, grouped and ordered as the desktop list orders its rows: READY by the
 * hub's queue, BACKLOG by its backlog. */
export function glanceListGroups(
  chunks: readonly ChunkSummary[],
  questions: readonly QuestionView[],
  decisions: readonly DecisionView[],
  readyOrder: readonly { readonly chunk_id: string }[],
  backlogOrder: readonly { readonly chunk_id: string }[],
  runnerHosts: ReadonlyMap<string, string>,
  projectColors: ReadonlyMap<string, string>,
  now: number,
  workItems: ReadonlyMap<string, WorkItemsView>,
): GlanceListGroups {
  const asks = new Map(questions.map((question) => [question.chunk_id, question.question]));
  const gated = new Set(decisions.map((decision) => decision.chunk_id));
  const byLane = new Map<string, GlanceRow[]>();
  for (const chunk of chunks) {
    const lane = chunkLane(chunk);
    const row: GlanceRow = {
      chunkId: chunk.chunk_id,
      shortId: compactRef(chunk.chunk_id),
      lane,
      status: chunk.status,
      statusWord: chunk.status === 'paused' || chunk.status === 'stopped' ? chunk.status : null,
      node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
      runner: chunk.runner_id ? runnerBoardParts(chunk.runner_id, chunk.runner_name, runnerHosts.get(chunk.runner_id) ?? null) : null,
      gated: gated.has(chunk.chunk_id),
      ask: asks.get(chunk.chunk_id) ?? null,
      ...rowWork(chunk, workItems),
      costUsd: chunk.cost?.cost_usd ?? 0,
      costPartial: chunk.cost?.cost_partial ?? false,
      estimatedCostUsd: chunk.cost?.estimated_cost_usd ?? null,
      projectColor: projectColors.get(chunk.project_id ?? '') ?? null,
    };
    if (lane === DONE_LANE) {
      const age = ageMs(chunk.completed_at, now);
      if (age === null || age > DONE_TODAY_WINDOW_MS) continue;
    }
    byLane.set(lane, [...(byLane.get(lane) ?? []), row]);
  }
  const lane = (key: string): GlanceRow[] => byLane.get(key) ?? [];
  return {
    live: [...lane('needs'), ...lane('waiting'), ...lane('running'), ...lane(UNKNOWN_LANE)],
    ready: byRank(lane('ready'), readyOrder),
    backlog: byRank(lane('notready'), backlogOrder),
    done: lane(DONE_LANE),
  };
}
