import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { FleetLiveUpdates, asyncState, asyncStateOf, injectNowSignal, type ChunkSummary, type KitAsyncStateValue } from 'fleet';
import { injectHubBoardChunksQuery } from '../../core/chunks.query';
import { injectHubChunkCountsQuery } from '../chunks/chunk-counts.query';
import { injectHubFleetSpendQuery } from '../fleet-spend/fleet-spend.query';
import { injectHubHealthQuery } from '../health/health.query';
import { injectHubQueueQuery } from '../queue/queue.query';
import { injectHubQuestionsQuery } from '../questions/questions.query';
import { injectHubDecisionsQuery } from '../gates/gates.query';
import { injectHubRunnersQuery } from '../../runners/runners.query';

import { startOfLocalDayIso } from '../../core/local-day';
import { doneTodayRows, doneTodayTotal, glanceVitals, inMotionRows, liveRunners, needsYouRows, terminalTotal, upNextRows } from './glance-board.model';
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
   * already reads — `injectHubBoardChunksQuery`, `injectHubQueueQuery`,
   * `injectHubQuestionsQuery`, `injectHubDecisionsQuery`, `injectHubRunnersQuery`, `injectHubHealthQuery`,
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
  private readonly chunksQuery = injectHubBoardChunksQuery();
  private readonly countsQuery = injectHubChunkCountsQuery();
  private readonly queueQuery = injectHubQueueQuery();
  private readonly questionsQuery = injectHubQuestionsQuery();
  private readonly decisionsQuery = injectHubDecisionsQuery();
  private readonly runnersQuery = injectHubRunnersQuery();
  private readonly health = injectHubHealthQuery();
  private readonly live = inject(FleetLiveUpdates);

  /** The fleet-wide spend-since read — the same local-midnight
   * window the titlebar's own cell reads (`startOfLocalDayIso`), so the two
   * never disagree and share one query-cache entry. */
  protected readonly spendToday = injectHubFleetSpendQuery(() => startOfLocalDayIso());

  private readonly chunks = computed<readonly ChunkSummary[]>(() => this.chunksQuery.data() ?? []);
  private readonly questions = computed(() => this.questionsQuery.data() ?? []);
  private readonly decisions = computed(() => this.decisionsQuery.data() ?? []);
  private readonly runners = computed(() => liveRunners(this.runnersQuery.data() ?? []));
  private readonly now = injectNowSignal(60_000);

  /**
   * Open asks first (the more specific "why"), then open gates, then any chunk
   * in a human-attention tone (`chunk-lanes.ts`'s `STATUS_TONE` — `waiting` or
   * `needs`) neither has already covered — folded into one attention-ordered
   * list (the mock's "Needs you"), deduped by chunk id so a parked chunk with an
   * open ask or gate shows once, not twice.
   */
  protected readonly needsYou = computed<readonly AttentionRow[]>(() => needsYouRows(this.questions(), this.decisions(), this.chunks()));

  /** Chunks whose tone is `running` (`STATUS_TONE`'s running lane: `running` +
   * `delivering`) — the mock's "In motion". */
  protected readonly inMotion = computed<readonly MotionRow[]>(() => inMotionRows(this.chunks()));

  /** READY chunks in the hub's dispatch order. The queue is the ordering fact;
   * the chunk list confirms each entry is still currently ready. */
  protected readonly upNext = computed<readonly UpNextRow[]>(() => upNextRows(this.queueQuery.data() ?? [], this.chunks()));

  /** Terminal chunks completed in the rolling previous 24 hours, newest first.
   * `ageMs` rejects missing, malformed, and meaningfully future instants; the
   * injected clock makes the cutoff advance without a fresh fleet-list read. */
  protected readonly doneToday = computed<readonly DoneRow[]>(() => doneTodayRows(this.chunks(), this.now()));

  /** Every terminal chunk the fleet has ever held — the board list omits old `done`
   * rows, so the all-time total comes from the counts read. Used as Done today's
   * visible/total header count, so an empty fleet still states `0/0`. */
  protected readonly terminalCount = computed(() => terminalTotal(this.countsQuery.data()));

  /** Each panel's async state, derived independently (AC 4) — a panel withholds
   * its empty copy on its own reads' loading/error, regardless of the other
   * panels. "Needs you" folds in the questions and decisions reads (an ask or a
   * gate can arrive before or after the chunk list settles); "In motion" and "Done today" are both
   * slices of the same chunks read alone; "Up next" owns both the chunk and
   * queue reads; spend is its own query. */
  protected readonly needsYouState = computed<KitAsyncStateValue>(() =>
    asyncStateOf([this.chunksQuery, this.questionsQuery, this.decisionsQuery], this.needsYou().length === 0),
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

  /** The terminal denominator is meaningful only once the counts read succeeded:
   * before then its empty fallback would falsely advertise `0/0`. */
  protected readonly doneTodayTotal = computed<number | null>(() =>
    doneTodayTotal(this.countsQuery.isPending(), this.countsQuery.isError(), this.terminalCount()),
  );

  /** Never `'empty'`: the spend endpoint returns a zeroed aggregate rather than
   * no row, so the "—" placeholder is only ever the pre-resolution rest state
   * the query-less `@if` used to show — a single-resource read, same reasoning
   * as `graph-detail.ts`'s own `state`. */
  protected readonly spendState = computed<KitAsyncStateValue>(() => asyncState(this.spendToday, false));

  /** The vitals strip's four numbers: the two attention/motion counts above,
   * the fleet registry's online fraction, and the live spine's connection —
   * the same connection-state fold `App`'s titlebar reads (`app.ts`). */
  protected readonly vitals = computed<Vitals>(() =>
    glanceVitals(this.runners(), this.live.status(), this.health.isError(), this.needsYou().length, this.inMotion().length),
  );
}
