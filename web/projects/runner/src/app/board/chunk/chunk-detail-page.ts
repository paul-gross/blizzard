import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import {
  asyncState,
  ChunkArtifacts,
  ChunkAwaitingHuman,
  ChunkFacts,
  ChunkIssuePane,
  ChunkTimeline,
  ChunkTokenBreakdown,
  deriveWorkItemsState,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBackBar,
  type KitChipOption,
  KitChips,
  KitPanel,
  type runnerApi,
  type WorkItemsState,
} from 'fleet';
import {
  injectChunkDetailQuery,
  injectChunkWorkItemsDetailQuery,
  injectRunnerLeasesQuery,
  TranscriptPanel,
} from 'local-panel';

/**
 * The `/board/chunk/:chunkId` route (issue #318) — the runner-local chunk
 * detail page: work item, issues, node history, asks · decisions, and
 * artifacts, composed from the same presentational `fleet` sections the hub
 * board's own `ChunkGeneralTab` arranges (`bzh:frontend-kit`), plus the
 * transcripts and per-attempt selection that used to live inline in the
 * dock (`MachineDetail`, issue #185) — moved here per D2/D4, a deliberate
 * replacement rather than a regression.
 *
 * A container mapping its three reads down to presentational children
 * (`bzh:frontend-container-presentational`): {@link injectChunkDetailQuery}
 * for the widened aggregate ({@link ChunkFacts} and siblings all declare
 * `detail: hubApi.ChunkDetail` — TypeScript is structural and the runner's
 * `ChunkDetailView` carries every field that type requires, so no
 * runner-local wrapper type is owed), {@link injectChunkWorkItemsDetailQuery}
 * for the full-fidelity work-item read ({@link ChunkIssuePane}'s
 * `WorkItemsState` triad — deliberately not the severable
 * `injectChunkTitleQuery` the board's list rows use, since this section
 * renders a real error state rather than silently dropping one), and
 * {@link injectRunnerLeasesQuery} for the transcript section's attempt
 * picker. The chunk id rides the URL's path (`:chunkId`); the attempt
 * selector stays on the existing `?attempt=` query param (D4) — the same
 * param `panel-selection.ts` reads on `/board`, read from this new place
 * rather than re-encoded.
 */
@Component({
  selector: 'app-chunk-detail-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ChunkArtifacts,
    ChunkAwaitingHuman,
    ChunkFacts,
    ChunkIssuePane,
    ChunkTimeline,
    ChunkTokenBreakdown,
    KitAsyncState,
    KitBackBar,
    KitChips,
    KitPanel,
    RouterLink,
    TranscriptPanel,
  ],
  template: `
    <div class="page" data-testid="chunk-detail-page">
      <a class="back-row" routerLink="/board" [queryParams]="backQueryParams()" data-testid="chunk-detail-back">
        <fleet-kit-back-bar label="Board" />
      </a>
      <fleet-kit-async-state
        [state]="detailState()"
        loadingText="LOADING…"
        loadingTestid="chunk-detail-page-loading"
        errorText="FAILED TO LOAD CHUNK"
        errorTestid="chunk-detail-page-error"
      >
        @if (detail(); as d) {
          <fleet-kit-panel class="section" data-testid="section-work-item" label="work item" [count]="pointerCount() || null">
            <fleet-chunk-detail-facts [detail]="d">
              <fleet-chunk-detail-token-breakdown token-breakdown [detail]="d" />
            </fleet-chunk-detail-facts>
          </fleet-kit-panel>
          <fleet-kit-panel class="section" data-testid="section-issues" label="issues">
            <fleet-chunk-detail-issue-pane [workItems]="workItems()" placement="inline" />
          </fleet-kit-panel>
          <fleet-kit-panel class="section" data-testid="section-node-history" label="node history">
            <fleet-chunk-detail-timeline [detail]="d" />
          </fleet-kit-panel>
          <fleet-kit-panel class="section" data-testid="section-asks" label="asks · decisions">
            <fleet-chunk-detail-awaiting-human [detail]="d" />
          </fleet-kit-panel>
          <fleet-kit-panel class="section" data-testid="section-artifacts" label="artifacts">
            <fleet-chunk-detail-artifacts [detail]="d" [expandable]="true" />
          </fleet-kit-panel>
          <fleet-kit-panel class="section transcript-section" data-testid="section-transcript" label="transcript">
            <fleet-kit-async-state
              [state]="attemptsState()"
              loadingText="LOADING…"
              loadingTestid="attempts-loading"
              emptyText="NO RECENT ATTEMPTS ON THIS MACHINE"
              emptyTestid="attempts-empty"
            >
              @if (attemptOptions().length > 1) {
                <div class="attempts" data-testid="attempt-tabs">
                  <fleet-kit-chips
                    [options]="attemptOptions()"
                    [selectedValue]="activeAttemptLeaseId()"
                    (choose)="selectAttempt($event)"
                  />
                </div>
              }
              @if (activeAttemptLeaseId(); as leaseId) {
                <local-transcript-panel [leaseId]="leaseId" />
              }
            </fleet-kit-async-state>
          </fleet-kit-panel>
        }
      </fleet-kit-async-state>
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
      overflow-y: auto;
    }
    .page {
      position: relative;
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 8px;
    }
    .back-row {
      flex: none;
      text-decoration: none;
    }
    fleet-kit-panel.section {
      flex: none;
    }
    fleet-kit-panel.transcript-section {
      display: flex;
      flex-direction: column;
      height: 480px;
    }
    .attempts {
      flex: none;
      padding-bottom: 6px;
    }
  `,
})
export class ChunkDetailPage {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  /** The route's own `:chunkId` path segment — structurally never null once
   * this component is instantiated (the route requires the segment), but
   * left nullable rather than coerced to `''` so it stays the sentinel
   * {@link injectChunkDetailQuery}/{@link injectChunkWorkItemsDetailQuery}
   * both declare (`enabled: id !== null`) — an empty string reads as a real,
   * if pathological, id to either, not "no id yet". */
  protected readonly chunkId = toSignal(
    this.route.paramMap.pipe(map((params) => params.get('chunkId'))),
    { initialValue: this.route.snapshot.paramMap.get('chunkId') },
  );

  /** The `?attempt=` query param — the requested attempt lease, before falling
   * back to the chunk's newest (D4: the existing `panel-selection.ts` param,
   * read from this new place, never a second encoding of it). */
  private readonly requestedAttempt = toSignal(
    this.route.queryParamMap.pipe(map((params) => params.get('attempt'))),
    { initialValue: this.route.snapshot.queryParamMap.get('attempt') },
  );

  private readonly detailQuery = injectChunkDetailQuery(() => this.chunkId());
  private readonly workItemsQuery = injectChunkWorkItemsDetailQuery(() => this.chunkId());
  private readonly leasesQuery = injectRunnerLeasesQuery();

  protected readonly detail = computed(() => this.detailQuery.data());

  protected readonly detailState = computed<KitAsyncStateValue>(() => asyncState(this.detailQuery, false));

  protected readonly pointerCount = computed(() => this.detail()?.work_refs?.length ?? 0);

  /** The open chunk's related work items + fetch state for the Issue pane —
   * the same {@link deriveWorkItemsState} fold `fleet`'s own `ChunkDetail`
   * container and the hub's `chunk-page.ts` use. */
  protected readonly workItems = computed<WorkItemsState>(() => deriveWorkItemsState(this.workItemsQuery));

  /** This chunk's attempts, oldest → newest — the server orders actives first
   * then recent-closed, so reversing the filtered slice restores oldest-first
   * (mirrors `local-panel.ts`'s own `machineChunks` fold). */
  protected readonly chunkLeases = computed<readonly runnerApi.LeaseView[]>(() =>
    [...(this.leasesQuery.data() ?? [])].filter((lease) => lease.chunk_id === this.chunkId()).reverse(),
  );

  protected readonly attemptsState = computed<KitAsyncStateValue>(() =>
    asyncState(this.leasesQuery, this.chunkLeases().length === 0),
  );

  /** One selectable chip per attempt, keyed by lease id and labelled with the
   * attempt ordinal + its state — the same shape the dock's own attempt tabs
   * used before this phase moved them here. */
  protected readonly attemptOptions = computed<readonly KitChipOption[]>(() =>
    this.chunkLeases().map((att) => ({
      value: att.lease_id,
      label: `a${att.epoch} ${att.state === 'closed' ? (att.closure_reason ?? 'closed') : att.state}`,
      testid: 'attempt-tab',
    })),
  );

  /** The attempt whose transcript renders — the requested pick when it still
   * names an attempt of this chunk, else the newest attempt. */
  protected readonly activeAttemptLeaseId = computed<string | null>(() => {
    const leases = this.chunkLeases();
    const newest = leases.at(-1) ?? null;
    const wanted = this.requestedAttempt();
    if (wanted !== null && leases.some((att) => att.lease_id === wanted)) return wanted;
    return newest?.lease_id ?? null;
  });

  /** The back link's query params — restores `/board`'s own `?chunk=&attempt=`
   * selection (`panel-selection.ts`) to exactly what this page had open,
   * rather than landing back on the board with no chunk selected. */
  protected readonly backQueryParams = computed(() => ({
    chunk: this.chunkId(),
    attempt: this.activeAttemptLeaseId(),
  }));

  /** Write an attempt pick to the URL's `?attempt=` param — a client-side
   * navigation that stays on this route. */
  protected selectAttempt(leaseId: string): void {
    void this.router.navigate([], { relativeTo: this.route, queryParams: { attempt: leaseId }, queryParamsHandling: 'merge' });
  }
}
