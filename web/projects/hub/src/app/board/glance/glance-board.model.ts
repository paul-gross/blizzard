import { STATUS_TONE, ageMs, compactRef, type ChunkCountsView, type ChunkSummary, type DecisionView, type QuestionView, type QueuePeekEntry, type RunnerView, type SseStatus } from 'fleet';

import type { AttentionRow, DoneRow, MotionRow, UpNextRow, Vitals } from './glance-view';

/** How far back "Done today" reaches — a rolling window, not a calendar day. */
const DONE_TODAY_WINDOW_MS = 24 * 60 * 60 * 1000;

/** The runners still on the fleet registry — a retired runner never counts toward runners-up. */
export function liveRunners(runners: readonly RunnerView[]): readonly RunnerView[] {
  return runners.filter((runner) => !runner.retired);
}

/**
 * Open asks first (the more specific "why"), then open gates, then any chunk in a human-attention tone
 * (`STATUS_TONE` — `waiting` or `needs`) neither has already covered — one attention-ordered list,
 * deduped by chunk id so a parked chunk with an open ask or gate shows once, not twice.
 */
export function needsYouRows(
  questions: readonly QuestionView[],
  decisions: readonly DecisionView[],
  chunks: readonly ChunkSummary[],
): readonly AttentionRow[] {
  const rows = new Map<string, AttentionRow>();
  for (const question of questions) {
    rows.set(question.chunk_id, {
      chunkId: question.chunk_id,
      shortId: compactRef(question.chunk_id),
      runnerId: question.runner_id,
      tone: 'waiting',
      pillLabel: 'ask',
      sub: question.question,
    });
  }
  const runnerOf = new Map(chunks.map((chunk) => [chunk.chunk_id, chunk.runner_id ?? null]));
  for (const decision of decisions) {
    if (rows.has(decision.chunk_id)) continue;
    rows.set(decision.chunk_id, {
      chunkId: decision.chunk_id,
      shortId: compactRef(decision.chunk_id),
      runnerId: runnerOf.get(decision.chunk_id) ?? null,
      tone: 'waiting',
      pillLabel: 'gate',
      sub: decision.node_name,
    });
  }
  for (const chunk of chunks) {
    if (rows.has(chunk.chunk_id)) continue;
    const tone = STATUS_TONE[chunk.status];
    if (tone !== 'needs' && tone !== 'waiting') continue;
    rows.set(chunk.chunk_id, {
      chunkId: chunk.chunk_id,
      shortId: compactRef(chunk.chunk_id),
      runnerId: chunk.runner_id ?? null,
      tone,
      pillLabel: tone === 'needs' ? 'needs human' : 'waiting',
      sub: chunk.current_node_name ?? chunk.current_node_id ?? '—',
    });
  }
  return [...rows.values()];
}

/** Chunks in `STATUS_TONE`'s running lane (`running` + `delivering`) — the "In motion" rows. */
export function inMotionRows(chunks: readonly ChunkSummary[]): readonly MotionRow[] {
  return chunks
    .filter((chunk) => STATUS_TONE[chunk.status] === 'running')
    .map((chunk) => ({
      chunkId: chunk.chunk_id,
      shortId: compactRef(chunk.chunk_id),
      runnerId: chunk.runner_id ?? null,
      node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
      pillLabel: chunk.status === 'delivering' ? ('deliver' as const) : ('run' as const),
      costUsd: chunk.cost?.cost_usd ?? 0,
      costPartial: chunk.cost?.cost_partial ?? false,
      estimatedCostUsd: chunk.cost?.estimated_cost_usd ?? null,
    }));
}

/** READY chunks in the hub's dispatch order. The queue is the ordering fact; the chunk list confirms each
 * entry is still currently ready, so a queue entry with no ready chunk behind it drops. */
export function upNextRows(queue: readonly QueuePeekEntry[], chunks: readonly ChunkSummary[]): readonly UpNextRow[] {
  const chunksById = new Map(chunks.map((chunk) => [chunk.chunk_id, chunk]));
  return queue
    .map((entry) => chunksById.get(entry.chunk_id))
    .filter((chunk): chunk is ChunkSummary => chunk?.status === 'ready')
    .map((chunk) => ({
      chunkId: chunk.chunk_id,
      shortId: compactRef(chunk.chunk_id),
      node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
    }));
}

/** Terminal chunks (the hub's own `terminal` flag) completed in the rolling 24 hours before `now`, newest
 * first. `ageMs` rejects missing, malformed, and meaningfully future instants. */
export function doneTodayRows(chunks: readonly ChunkSummary[], now: number): readonly DoneRow[] {
  return chunks
    .flatMap((chunk) => {
      const age = ageMs(chunk.completed_at, now);
      if (!chunk.terminal || age === null || age > DONE_TODAY_WINDOW_MS) return [];
      return [{
        age,
        row: {
          chunkId: chunk.chunk_id,
          shortId: compactRef(chunk.chunk_id),
          // Only labeled pointers show, space-joined into one line: the "done today" glance is a denser,
          // read-only summary.
          pointerLabel: (chunk.work_refs ?? []).flatMap((p) => (p.label ? [p.label] : [])).join(' '),
        },
      }];
    })
    .sort((left, right) => left.age - right.age)
    .map(({ row }) => row);
}

/** Every terminal chunk the fleet has ever held, from the all-time counts read — `0` before it resolves. */
export function terminalTotal(counts: ChunkCountsView | undefined): number {
  return counts === undefined ? 0 : counts.terminal;
}

/** Done today's denominator — withheld (`null`) until the counts read succeeds, so its empty fallback never
 * falsely advertises `0/0`. */
export function doneTodayTotal(isPending: boolean, isError: boolean, total: number): number | null {
  return isPending || isError ? null : total;
}

/** The vitals strip: the attention and motion counts, the registry's online fraction, and the live spine's
 * connection label — `offline` only once the health read has failed while the stream is neither open nor
 * reconnecting. */
export function glanceVitals(
  runners: readonly RunnerView[],
  streamState: SseStatus,
  healthIsError: boolean,
  needsYouCount: number,
  runningCount: number,
): Vitals {
  const online = runners.filter((runner) => runner.online).length;
  const liveLabel =
    streamState === 'open'
      ? 'live'
      : streamState === 'reconnecting'
        ? 'reconnecting'
        : healthIsError
          ? 'offline'
          : 'connecting';
  return {
    needsYou: needsYouCount,
    running: runningCount,
    runnersUpLabel: `${online}/${runners.length}`,
    live: streamState === 'open',
    liveLabel,
  };
}
