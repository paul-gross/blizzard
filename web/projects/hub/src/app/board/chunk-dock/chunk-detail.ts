import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal } from '@angular/core';

import { type ChunkDetailAggregate, injectHubChunkDetailQuery, injectHubChunkWorkItemsQuery, injectRunnerHostNames, KitAsyncState, type KitAsyncStateValue, injectPendingMutationVariables, asyncState, type WorkItemsState } from 'fleet';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectProjectsQuery } from '../../core/projects.query';
import { type CompleteVars } from '../chunks/complete.mutations';
import { injectHubChunkControl } from '../chunks/hub-chunk-control';
import { injectAwaitingHumanService } from '../chunks/awaiting-human.service';
import { type ChunkPauseVars } from '../chunks/pause.mutations';
import { chunkCompleteMutationKey, chunkPauseMutationKey } from '../../core/mutation-keys';
import { openDetail, openWorkItems, pendingStatusOverride } from './chunk-detail.model';
import { ChunkDetailPanel } from './chunk-detail-panel';
import { ChunkDetailSkeleton } from './chunk-detail-skeleton';
import { injectShowProjectColors, openProjectColor } from '../../core/project-lens-colors';

/**
 * The chunk detail **container** (`bzh:frontend-container-presentational`) — owns the
 * detail query and the operator-action mutations, and renders {@link ChunkDetailPanel}
 * over them, or a rest state while no chunk is open — which the board, its one host, keeps out of
 * sight by closing the dock until a chunk is selected.
 *
 * Every action reports through two channels: `actionError` for a failure, and
 * `actionOutcome` for a non-failure result that still needs saying (a lost answer race,
 * naming the winner). Both clear on the next attempt and whenever a different chunk opens.
 */
@Component({
  selector: 'app-chunk-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDetailPanel, ChunkDetailSkeleton, KitAsyncState],
  templateUrl: './chunk-detail.html',
  styleUrl: './chunk-detail.css',
})
export class ChunkDetail {
  /** The selected chunk id, or `null` when the dock is closed. */
  readonly chunkId = input<string | null>(null);

  readonly boardChunkIds = input<ReadonlySet<string>>(new Set());

  /** The graphs view's path segments, forwarded to {@link ChunkDetailPanel.graphLinkBase};
   * `null` withholds the graph links. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  private readonly detailQuery = injectHubChunkDetailQuery(() => this.chunkId());
  private readonly workItemsQuery = injectHubChunkWorkItemsQuery(() => this.chunkId());
  /** The ⋮ menu's chunk-control port — failures land on {@link actionError}, and a
   * confirmed delete of the open chunk dismisses the dock. */
  protected readonly control = injectHubChunkControl({
    beginAction: () => this.beginAction(),
    fail: (message) => this.actionError.set(message),
    onDeleted: (chunkId) => {
      if (this.chunkId() === chunkId) this.dismiss.emit();
    },
  });
  private readonly meQuery = injectMeQuery();
  private readonly projectsQuery = injectProjectsQuery();
  protected readonly runnerHosts = injectRunnerHostNames();

  /** Whether the current identity may answer an open question (`question:answer`). */
  protected readonly canAnswer = computed(() => hasPermission(this.meQuery.data(), 'question:answer'));

  /** Whether the current identity may resolve an open gate decision (`gate:resolve`). */
  protected readonly canResolve = computed(() => hasPermission(this.meQuery.data(), 'gate:resolve'));

  /** Every chunk id a Pause/Resume mutation is currently pending for, with its variables
   * (`bzh:frontend-pending-override`). */
  private readonly pendingChunkPauses = injectPendingMutationVariables<ChunkPauseVars>(chunkPauseMutationKey);

  /** The same, for Complete. */
  private readonly pendingChunkCompletes = injectPendingMutationVariables<CompleteVars>(chunkCompleteMutationKey);

  /** The pending Pause or Complete status override, or `null` — {@link pendingStatusOverride}. */
  protected readonly overrideStatus = computed(() =>
    pendingStatusOverride(this.detail(), this.pendingChunkCompletes(), this.pendingChunkPauses()),
  );

  /** The chunk's rendered status — {@link overrideStatus} while it names one, else the
   * real `detail.status` (`bzh:frontend-pending-override`). */
  protected renderedStatus(detail: ChunkDetailAggregate): string {
    return this.overrideStatus() ?? (detail.status.kind === 'unknown' ? detail.status.raw : detail.status.kind);
  }

  /** The open chunk's last operator-action failure, or `null`. */
  protected readonly actionError = signal<string | null>(null);

  /** The awaiting-human saves — a lost race lands on {@link actionOutcome}; every other refusal stays on its form. */
  protected readonly awaitingHuman = injectAwaitingHumanService({
    begin: () => this.beginAction(),
    outcome: (message) => this.actionOutcome.set(message),
  });

  /** The open chunk's last operator-action **outcome** — a non-failure result that still
   * needs saying: a lost answer race, carrying the winning answer. Pinned by
   * `chunk-detail.spec.ts`'s "renders the winner’s name and answer as an outcome when the
   * answer race is lost". */
  protected readonly actionOutcome = signal<string | null>(null);

  constructor() {
    effect(() => {
      this.chunkId();
      this.beginAction();
    });
  }

  /** Clear both report channels together — every action starts here, and so does
   * opening a different chunk. */
  private beginAction(): void {
    this.actionError.set(null);
    this.actionOutcome.set(null);
  }

  /** The open chunk's aggregate, or `undefined` while closed / still loading. */
  protected readonly detail = computed(() => openDetail(this.chunkId(), this.detailQuery.data()));

  /** The open chunk's project's color — {@link openProjectColor}. */
  private readonly showProjectColors = injectShowProjectColors();
  protected readonly projectColor = computed(() =>
    openProjectColor(this.showProjectColors(), this.detail(), this.projectsQuery.data()),
  );

  /**
   * The detail read's async state. Read only once a chunk is open: the query is
   * disabled while `chunkId()` is `null`, and a disabled query reports `isPending()`
   * forever. Never `'empty'`: a single aggregate either resolves or errors.
   */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.detailQuery, false));

  /** The open chunk's related work items + fetch state for the Issue tab. A failed
   * read (unreachable hub / no work-source) becomes `error` so the tab shows a visible notice. */
  protected readonly workItems = computed<WorkItemsState>(() => openWorkItems(this.chunkId(), this.workItemsQuery));
}
