import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import {
  FleetLiveUpdates,
  STATUS_TONE,
  ageMs,
  asyncState,
  asyncStateOf,
  compactRef,
  injectHubChunksQuery,
  injectHubFleetSpendQuery,
  injectHubHealthQuery,
  injectHubQueueQuery,
  injectHubQuestionsQuery,
  injectHubRunnersQuery,
  injectNowSignal,
  type ChunkSummary,
  type KitAsyncStateValue,
} from 'fleet';

import { startOfLocalDayIso } from '../../local-day';
import { GlanceView, type AttentionRow, type DoneRow, type MotionRow, type UpNextRow, type Vitals } from './glance-view';

/**
 * The mobile glance board (mock screen C, `../docs/designs/mobile/core-flows.html`)
 * — a container (`bzh:frontend-container-presentational`): it owns every read the
 * shell needs and folds them into the attention-ordered buckets the
 * presentational {@link GlanceView} renders, so "does anything need me?" answers
 * in one scroll rather than a three-column scan.
 *
 * A routed page, not a branch inside {@link BoardPage}: the hub's route table
 * (`app.routes.ts`) mounts this component at `board` guarded by `fleet`'s
 * `matchesMobileViewport`, with `BoardPage`'s own unguarded `board` entry as the
 * desktop fallback — the same URL, two shells, the fork made once in the route
 * table rather than per-page (see `app.routes.ts`'s doc comment).
 *
 * Every number and row here comes from queries {@link BoardPage}'s desktop shell
   * already reads — `injectHubChunksQuery`, `injectHubQueueQuery`,
   * `injectHubQuestionsQuery`, `injectHubRunnersQuery`, `injectHubHealthQuery`,
   * `injectHubFleetSpendQuery` —
 * plus the same `FleetLiveUpdates` spine the app root starts: no new backend
 * plumbing, per the mobile README's shared-guts inventory ("Reuses chunks.query
 * … status vocabulary from chunk-lanes. New: attention-sort, vitals strip,
 * mobile board shell").
 *
   * The ready queue supplies its own dispatch order, rather than asking the chunk
   * list to invent one. Terminal chunks carry `completed_at`, so the rolling
   * "Done today" window can remain a fleet-list read rather than an N+1 detail
   * lookup.
 */
@Component({
  selector: 'app-glance-board',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GlanceView],
  templateUrl: './glance-board.html',
  // A routed page fills the router outlet area, same as `BoardPage`'s own
  // `:host` — `GlanceView`'s `:host { height: 100% }` needs this to resolve
  // against, and `App`'s flex-column layout needs `flex: 1` to give this
  // page the remaining space rather than its content's intrinsic size.
  styleUrl: './glance-board.css',
})
export class GlanceBoard {
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly queueQuery = injectHubQueueQuery();
  private readonly questionsQuery = injectHubQuestionsQuery();
  private readonly runnersQuery = injectHubRunnersQuery();
  private readonly health = injectHubHealthQuery();
  private readonly live = inject(FleetLiveUpdates);

  /** The fleet-wide spend-since read (issue #60) — the same local-midnight
   * window the titlebar's own cell reads (`startOfLocalDayIso`), so the two
   * never disagree and share one query-cache entry. */
  protected readonly spendToday = injectHubFleetSpendQuery(() => startOfLocalDayIso());

  private readonly chunks = computed<readonly ChunkSummary[]>(() => this.chunksQuery.data() ?? []);
  private readonly questions = computed(() => this.questionsQuery.data() ?? []);
  private readonly runners = computed(() => this.runnersQuery.data() ?? []);
  private readonly now = injectNowSignal(60_000);

  /**
   * Open asks first (the more specific "why"), then any chunk in a
   * human-attention tone (`chunk-lanes.ts`'s `STATUS_TONE` — `waiting` or
   * `needs`) an open ask hasn't already covered — folded into one
   * attention-ordered list (the mock's "Needs you"), deduped by chunk id so a
   * parked chunk with an open ask shows once, not twice.
   */
  protected readonly needsYou = computed<readonly AttentionRow[]>(() => {
    const rows = new Map<string, AttentionRow>();
    for (const question of this.questions()) {
      rows.set(question.chunk_id, {
        chunkId: question.chunk_id,
        shortId: compactRef(question.chunk_id),
        runnerId: question.runner_id,
        tone: 'waiting',
        pillLabel: 'ask',
        sub: question.question,
      });
    }
    for (const chunk of this.chunks()) {
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
  });

  /** Chunks whose tone is `running` (`STATUS_TONE`'s running lane: `running` +
   * `delivering`) — the mock's "In motion". */
  protected readonly inMotion = computed<readonly MotionRow[]>(() =>
    this.chunks()
      .filter((chunk) => STATUS_TONE[chunk.status] === 'running')
      .map((chunk) => ({
        chunkId: chunk.chunk_id,
        shortId: compactRef(chunk.chunk_id),
        runnerId: chunk.runner_id ?? null,
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
        pillLabel: chunk.status === 'delivering' ? ('deliver' as const) : ('run' as const),
        costUsd: chunk.cost?.cost_usd ?? 0,
        costPartial: chunk.cost?.cost_partial ?? false,
      })),
  );

  /** READY chunks in the hub's dispatch order. The queue is the ordering fact;
   * the chunk list confirms each entry is still currently ready. */
  protected readonly upNext = computed<readonly UpNextRow[]>(() => {
    const chunksById = new Map(this.chunks().map((chunk) => [chunk.chunk_id, chunk]));
    return (this.queueQuery.data() ?? [])
      .map((entry) => chunksById.get(entry.chunk_id))
      .filter((chunk): chunk is ChunkSummary => chunk?.status === 'ready')
      .map((chunk) => ({
        chunkId: chunk.chunk_id,
        shortId: compactRef(chunk.chunk_id),
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
      }));
  });

  /** Terminal chunks completed in the rolling previous 24 hours, newest first.
   * `ageMs` rejects missing, malformed, and meaningfully future instants; the
   * injected clock makes the cutoff advance without a fresh fleet-list read. */
  protected readonly doneToday = computed<readonly DoneRow[]>(() =>
    this.chunks()
      .flatMap((chunk) => {
        const age = ageMs(chunk.completed_at, this.now());
        if (STATUS_TONE[chunk.status] !== 'done' || age === null || age > 24 * 60 * 60 * 1000) return [];
        return [{
          age,
          row: {
            chunkId: chunk.chunk_id,
            shortId: compactRef(chunk.chunk_id),
            // Only labeled pointers show — the same filter the desktop board's card applies
            // (board-shell.ts). Unlike that card (issue #176), this row's own `DoneRow` type
            // keeps its labels space-joined into one line: the "done today" glance is a
            // denser, read-only summary, not the card these rows are a distinct type from.
            pointerLabel: (chunk.work_refs ?? []).flatMap((p) => (p.label ? [p.label] : [])).join(' '),
          },
        }];
      })
      .sort((left, right) => left.age - right.age)
      .map(({ row }) => row),
  );

  /** Every terminal chunk, including older rows and rows whose completion instant
   * cannot be rendered in the rolling window. Used as Done today's visible/total
   * header count, so an empty fleet still states `0/0`. */
  protected readonly terminalCount = computed(() => this.chunks().filter((chunk) => STATUS_TONE[chunk.status] === 'done').length);

  /** Each panel's async state, derived independently (AC 4) — a panel withholds
   * its empty copy on its own reads' loading/error, regardless of the other
   * panels. "Needs you" folds in the questions read (an ask can arrive before
   * or after the chunk list settles); "In motion" and "Done today" are both
   * slices of the same chunks read alone; "Up next" owns both the chunk and
   * queue reads; spend is its own query. */
  protected readonly needsYouState = computed<KitAsyncStateValue>(() =>
    asyncStateOf([this.chunksQuery, this.questionsQuery], this.needsYou().length === 0),
  );

  protected readonly inMotionState = computed<KitAsyncStateValue>(() =>
    asyncState(this.chunksQuery, this.inMotion().length === 0),
  );

  protected readonly upNextState = computed<KitAsyncStateValue>(() =>
    asyncStateOf([this.chunksQuery, this.queueQuery], this.upNext().length === 0),
  );

  protected readonly doneTodayState = computed<KitAsyncStateValue>(() =>
    asyncState(this.chunksQuery, this.doneToday().length === 0),
  );

  /** The terminal denominator is meaningful only once the chunks read succeeded:
   * before then its empty fallback would falsely advertise `0/0`. */
  protected readonly doneTodayTotal = computed<number | null>(() =>
    this.chunksQuery.isPending() || this.chunksQuery.isError() ? null : this.terminalCount(),
  );

  /** Never `'empty'`: the spend endpoint returns a zeroed aggregate rather than
   * no row, so the "—" placeholder is only ever the pre-resolution rest state
   * the query-less `@if` used to show — a single-resource read, same reasoning
   * as `graph-detail.ts`'s own `state`. */
  protected readonly spendState = computed<KitAsyncStateValue>(() => asyncState(this.spendToday, false));

  /** The vitals strip's four numbers: the two attention/motion counts above,
   * the fleet registry's online fraction, and the live spine's connection —
   * the same connection-state fold `App`'s titlebar reads (`app.ts`). */
  protected readonly vitals = computed<Vitals>(() => {
    const runners = this.runners();
    const online = runners.filter((runner) => runner.online).length;
    const streamState = this.live.status();
    const liveLabel =
      streamState === 'open'
        ? 'live'
        : streamState === 'reconnecting'
          ? 'reconnecting'
          : this.health.isError()
            ? 'offline'
            : 'connecting';
    return {
      needsYou: this.needsYou().length,
      running: this.inMotion().length,
      runnersUpLabel: `${online}/${runners.length}`,
      live: streamState === 'open',
      liveLabel,
    };
  });
}
