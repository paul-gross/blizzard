import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import {
  asyncState,
  ChunkArtifacts,
  deriveWorkItemsState,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBackBar,
  type KitChipOption,
  KitPanel,
  KitTabs,
  type KitTabOption,
  type runnerApi,
  type WorkItemsState,
} from 'fleet';
import { injectChunkDetailQuery, injectChunkWorkItemsDetailQuery, injectRunnerLeasesQuery } from 'local-panel';

import { type RunnerChunkDetailTab, injectChunkDetailSelection } from './chunk-detail-selection';
import { ChunkGeneralTab } from './chunk-general-tab';
import { ChunkTranscriptsTab } from './chunk-transcripts-tab';

/**
 * The `/board/chunk/:chunkId` route (issue #318, tabbed follow-up) — the
 * runner-local chunk detail page: work item, issues, node history, asks ·
 * decisions, artifacts, and the per-attempt transcript (`MachineDetail`,
 * issue #185, moved here per D2/D4), now split across three tabs — General,
 * Artifacts, Transcripts — selected through {@link injectChunkDetailSelection}
 * (`?tab=`), the same shape the hub's own `chunk-page.ts` gives its own tab
 * strip.
 *
 * A container mapping its three reads down to presentational children
 * (`bzh:frontend-container-presentational`): {@link injectChunkDetailQuery}
 * for the whole aggregate ({@link ChunkGeneralTab} and siblings all declare
 * `detail: hubApi.ChunkDetail` — the runner's proxy declares that same shared
 * model, so the payload is that type field for field, escalation included,
 * and no runner-local wrapper type is owed), {@link injectChunkWorkItemsDetailQuery}
 * for the full-fidelity work-item read ({@link ChunkGeneralTab}'s
 * `WorkItemsState` triad — deliberately not the severable
 * `injectChunkTitleQuery` the board's list rows use, since this section
 * renders a real error state rather than silently dropping one), and
 * {@link injectRunnerLeasesQuery} for the Transcripts tab's attempt picker.
 * The chunk id rides the URL's path (`:chunkId`); the attempt selector rides
 * `?attempt=` (D4), which this route owns outright — the only site that reads
 * it and the only one that writes it. `?tab=` and `?attempt=` are independent
 * params on the same URL: {@link injectChunkDetailSelection}'s `select` merges
 * rather than replaces, so switching tabs never drops the open attempt.
 * General and Transcripts each move their own layout into a presentational
 * sibling ({@link ChunkGeneralTab}, {@link ChunkTranscriptsTab}) — the same
 * split the hub's tabs make; Artifacts stays inline since it is already one
 * `fleet` component behind its own panel, nothing this container would gain
 * from a further extraction. Together this is what keeps this container
 * under `web:structural-gate`'s line cap.
 */
const TAB_OPTIONS: readonly KitTabOption[] = [
  { value: 'general', label: 'General', testid: 'tab-general' },
  { value: 'artifacts', label: 'Artifacts', testid: 'tab-artifacts' },
  { value: 'transcripts', label: 'Transcripts', testid: 'tab-transcripts' },
];

@Component({
  selector: 'app-chunk-detail-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifacts, ChunkGeneralTab, ChunkTranscriptsTab, KitAsyncState, KitBackBar, KitPanel, KitTabs, RouterLink],
  template: `
    <div class="page" data-testid="chunk-detail-page">
      <a class="back-row" routerLink="/board" [queryParams]="backQueryParams()" data-testid="chunk-detail-back">
        <fleet-kit-back-bar label="Board" />
      </a>
      <div class="body">
        <fleet-kit-async-state
          [state]="detailState()"
          loadingText="LOADING…"
          loadingTestid="chunk-detail-page-loading"
          errorText="FAILED TO LOAD CHUNK"
          errorTestid="chunk-detail-page-error"
        >
          @if (detail(); as d) {
            <fleet-kit-tabs [options]="tabOptions" [activeValue]="tab()" (choose)="onChooseTab($event)" />
            @switch (tab()) {
              @case ('general') {
                <app-chunk-general-tab [detail]="d" [workItems]="workItems()" />
              }
              @case ('artifacts') {
                <fleet-kit-panel class="section" data-testid="section-artifacts" label="artifacts">
                  <!-- Heading suppressed: the enclosing panel's label already
                       says "artifacts". The default stays true for the board
                       dock, which wraps this in a bare <section> instead. -->
                  <fleet-chunk-detail-artifacts [detail]="d" [expandable]="true" [heading]="false" />
                </fleet-kit-panel>
              }
              @case ('transcripts') {
                <app-chunk-transcripts-tab
                  [attemptsState]="attemptsState()"
                  [attemptOptions]="attemptOptions()"
                  [activeAttemptLeaseId]="activeAttemptLeaseId()"
                  (selectAttempt)="selectAttempt($event)"
                />
              }
            }
          }
        </fleet-kit-async-state>
      </div>
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
      overflow-y: auto;
    }
    /* Fills the host even with nothing loaded yet, so .body below has a real
       void to center a status line in — see .body. */
    .page {
      box-sizing: border-box;
      min-height: 100%;
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 8px;
    }
    .back-row {
      flex: none;
      text-decoration: none;
    }
    /* The tabs' own column, and the positioned ancestor {@link KitAsyncState}'s
       absolutely-centered status line resolves against. It is the back row's
       *sibling*, not its parent: centering against .page instead resolves against a
       box the 44px back bar is the only content of while the read is in flight, and
       the status line lands on top of it. */
    .body {
      position: relative;
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    fleet-kit-tabs {
      flex: none;
    }
    app-chunk-general-tab,
    app-chunk-transcripts-tab,
    fleet-kit-panel.section {
      flex: 1;
      min-height: 0;
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
   * back to the chunk's newest. This route's own param (D4), so a reload or a
   * shared link resolves to the same attempt. */
  private readonly requestedAttempt = toSignal(
    this.route.queryParamMap.pipe(map((params) => params.get('attempt'))),
    { initialValue: this.route.snapshot.queryParamMap.get('attempt') },
  );

  protected readonly tabOptions = TAB_OPTIONS;

  private readonly selection = injectChunkDetailSelection();

  protected readonly tab = this.selection.tab;

  protected onChooseTab(tab: string): void {
    this.selection.select(tab as RunnerChunkDetailTab);
  }

  private readonly detailQuery = injectChunkDetailQuery(() => this.chunkId());
  private readonly workItemsQuery = injectChunkWorkItemsDetailQuery(() => this.chunkId());
  private readonly leasesQuery = injectRunnerLeasesQuery();

  protected readonly detail = computed(() => this.detailQuery.data());

  protected readonly detailState = computed<KitAsyncStateValue>(() => asyncState(this.detailQuery, false));

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

  /** The back link's query params — restores `/board`'s own `?chunk=`
   * selection (`panel-selection.ts`) to the chunk this page had open, rather
   * than landing back on the board with nothing selected. `?attempt=` is not
   * carried: the board has no attempt selection to restore it into. */
  protected readonly backQueryParams = computed(() => ({ chunk: this.chunkId() }));

  /** Write an attempt pick to the URL's `?attempt=` param — a client-side
   * navigation that stays on this route and leaves `?tab=` untouched. */
  protected selectAttempt(leaseId: string): void {
    void this.router.navigate([], { relativeTo: this.route, queryParams: { attempt: leaseId }, queryParamsHandling: 'merge' });
  }
}
