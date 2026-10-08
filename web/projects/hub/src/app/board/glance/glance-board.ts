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
 * The mobile glance board — a container (`bzh:frontend-container-presentational`): it
 * owns every read the glance shell needs and folds them, through `glance-board.model.ts`,
 * into the attention-ordered buckets the presentational {@link GlanceView} renders.
 */
@Component({
  selector: 'app-glance-board',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GlanceView],
  templateUrl: './glance-board.html',
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

  /** The fleet-wide spend since local midnight (`startOfLocalDayIso`), cut from the minute tick. */
  protected readonly spendToday = injectHubFleetSpendQuery(() => startOfLocalDayIso(this.now()));

  private readonly chunks = computed<readonly ChunkSummary[]>(() => this.chunksQuery.data() ?? []);
  private readonly questions = computed(() => this.questionsQuery.data() ?? []);
  private readonly decisions = computed(() => this.decisionsQuery.data() ?? []);
  private readonly runners = computed(() => liveRunners(this.runnersQuery.data() ?? []));
  private readonly now = injectNowSignal(60_000);

  protected readonly needsYou = computed<readonly AttentionRow[]>(() => needsYouRows(this.questions(), this.decisions(), this.chunks()));

  protected readonly inMotion = computed<readonly MotionRow[]>(() => inMotionRows(this.chunks()));

  protected readonly upNext = computed<readonly UpNextRow[]>(() => upNextRows(this.queueQuery.data() ?? [], this.chunks()));

  protected readonly doneToday = computed<readonly DoneRow[]>(() => doneTodayRows(this.chunks(), this.now()));

  protected readonly terminalCount = computed(() => terminalTotal(this.countsQuery.data()));

  /** Each panel's async state, derived only from the reads that panel folds. */
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

  protected readonly doneTodayTotal = computed<number | null>(() =>
    doneTodayTotal(this.countsQuery.isPending(), this.countsQuery.isError(), this.terminalCount()),
  );

  /** Never `'empty'`: the spend read always resolves to an aggregate. */
  protected readonly spendState = computed<KitAsyncStateValue>(() => asyncState(this.spendToday, false));

  protected readonly vitals = computed<Vitals>(() =>
    glanceVitals(this.runners(), this.live.status(), this.health.isError(), this.needsYou().length, this.inMotion().length),
  );
}
