import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, injectNowSignal, injectRunnerHostNames, type ChunkSummary, type KitAsyncStateValue } from 'fleet';
import { injectHubBoardChunksQuery } from '../../core/chunks.query';
import { injectFleetSpendDays } from '../fleet-spend/fleet-spend-days';
import { injectHubBacklogQuery, injectHubQueueQuery } from '../queue/queue.query';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectProjectsQuery } from '../../core/projects.query';
import { projectColorsById } from '../board-page.model';
import { injectHubQuestionsQuery } from '../questions/questions.query';
import { injectHubDecisionsQuery } from '../gates/gates.query';
import { injectHubRunnersQuery } from '../../core/runners.query';
import { injectBoardWorkItemsQuery } from '../../core/work-items/board-work-items.query';

import { glanceListGroups, glanceVitals, liveRunners, type GlanceListGroups } from './glance-board.model';
import { GlanceView, type Vitals } from './glance-view';
import { injectShowProjectColors } from '../../core/project-lens-colors';

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
  private readonly queueQuery = injectHubQueueQuery();
  private readonly meQuery = injectMeQuery();
  private readonly canReorder = computed(() => hasPermission(this.meQuery.data(), 'queue:reorder'));
  /** The backlog's hub order — only readable with `queue:reorder`; without it the backlog keeps the chunk read's order. */
  private readonly backlogQuery = injectHubBacklogQuery(this.canReorder);
  private readonly questionsQuery = injectHubQuestionsQuery();
  private readonly decisionsQuery = injectHubDecisionsQuery();
  private readonly runnersQuery = injectHubRunnersQuery();
  private readonly runnerHosts = injectRunnerHostNames();
  private readonly projectsQuery = injectProjectsQuery();
  private readonly showProjectColors = injectShowProjectColors();
  private readonly projectColors = computed(() =>
    projectColorsById((this.showProjectColors() && this.projectsQuery.data()) || []),
  );

  private readonly now = injectNowSignal();

  /** The fleet-wide spend today and yesterday — the desktop rail's same two reads. */
  private readonly spend = injectFleetSpendDays();
  protected readonly spendToday = this.spend.today;
  protected readonly spendYesterday = this.spend.yesterday;

  private readonly chunks = computed<readonly ChunkSummary[]>(() => this.chunksQuery.data() ?? []);
  private readonly runners = computed(() => liveRunners(this.runnersQuery.data() ?? []));

  /** Every chunk's work items — the board's own batched read, so the glance and the board share one cache. */
  private readonly workItems = injectBoardWorkItemsQuery(() => this.chunks().map((chunk) => chunk.chunk_id));

  /** Every chunk as one ordered list of cards, as the desktop list orders its rows. */
  protected readonly list = computed<GlanceListGroups>(() =>
    glanceListGroups(
      this.chunks(),
      this.questionsQuery.data() ?? [],
      this.decisionsQuery.data() ?? [],
      this.queueQuery.data() ?? [],
      this.backlogQuery.data() ?? [],
      this.runnerHosts(),
      this.projectColors(),
      this.now(),
      this.workItems(),
    ),
  );

  protected readonly listState = computed<KitAsyncStateValue>(() => {
    const list = this.list();
    return asyncState(this.chunksQuery, list.live.length + list.ready.length + list.backlog.length + list.done.length === 0);
  });

  /** Never `'empty'`: the spend read always resolves to an aggregate. */
  protected readonly spendState = computed<KitAsyncStateValue>(() => asyncState(this.spendToday, false));

  protected readonly vitals = computed<Vitals>(() => glanceVitals(this.runners(), this.list()));
}
