import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import {
  type ArtifactView,
  asyncState,
  ChunkArtifactsPanel,
  ChunkPageHeader,
  ChunkPageShell,
  deriveWorkItemsState,
  filterArtifactsByStep,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBackBar,
  type KitChipOption,
  KitTabs,
  type KitTabOption,
  parseNodeStepKey,
  type runnerApi,
  sortArtifacts,
  STATUS_TONE,
  type WorkItemsState,
} from 'fleet';
import { injectChunkDetailQuery, injectChunkWorkItemsDetailQuery, injectRunnerLeasesQuery } from 'local-panel';

import { type RunnerChunkDetailTab, injectChunkDetailSelection } from './chunk-detail-selection';
import { ChunkGeneralTab } from './chunk-general-tab';
import { ChunkNodeHistoryTab } from './chunk-node-history-tab';
import { ChunkTranscriptsTab } from './chunk-transcripts-tab';

/**
 * The `/board/chunk/:chunkId` route (now tabbed, further widened for Node
 * history) — the runner-local chunk detail page: work item, issues, node history, asks ·
 * decisions, artifacts, and the per-attempt transcript (`MachineDetail`,
 * moved here per D2/D4), now split across four tabs — General, Node history, Artifacts,
 * Transcripts — selected through {@link injectChunkDetailSelection} (`?tab=`), the same
 * shape the hub's own `chunk-page.ts` gives its own tab strip.
 *
 * A container mapping its reads down to presentational children
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
 * it and the only one that writes it. `?tab=`, `?attempt=`, `?artifact=`, and
 * `?step=` are independent params on the same URL: every write through
 * {@link injectChunkDetailSelection} merges rather than replaces, so switching
 * tabs never drops another tab's own selection or the open attempt.
 * General, Node history, and Transcripts each move their own layout into a
 * presentational sibling ({@link ChunkGeneralTab}, {@link ChunkNodeHistoryTab},
 * {@link ChunkTranscriptsTab}) — the same split the hub's tabs make; Artifacts
 * stays inline since it is already one `fleet` component ({@link ChunkArtifactsPanel}),
 * nothing this container would gain from a further extraction. Together this
 * is what keeps this container under `web:lint`'s line cap.
 *
 * The Node history tab stops at each row's own artifacts — it does not wire a
 * per-step transcript the way the hub's own tab does: the hub's transcript
 * routes are declared `dependencies=[Depends(reject_runner_principal)]`, so a
 * runner-authenticated bearer is structurally refused there.
 */
const TAB_OPTIONS: readonly KitTabOption[] = [
  { value: 'general', label: 'General', testid: 'tab-general' },
  { value: 'node-history', label: 'Node history', testid: 'tab-node-history' },
  { value: 'artifacts', label: 'Artifacts', testid: 'tab-artifacts' },
  { value: 'transcripts', label: 'Transcripts', testid: 'tab-transcripts' },
];

@Component({
  selector: 'app-chunk-detail-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ChunkArtifactsPanel,
    ChunkGeneralTab,
    ChunkNodeHistoryTab,
    ChunkPageHeader,
    ChunkPageShell,
    ChunkTranscriptsTab,
    KitAsyncState,
    KitBackBar,
    KitTabs,
    RouterLink,
  ],
  templateUrl: './chunk-detail-page.html',
  styleUrl: './chunk-detail-page.css',
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

  /** The raw `?artifact=` param — forwarded to {@link ChunkArtifactsPanel} with no
   * lookup against the artifact store here. */
  protected readonly artifactKey = this.selection.artifactKey;

  /** The raw `?step=` param — forwarded to {@link ChunkNodeHistoryTab} with no lookup
   * against the timeline's own rows here. */
  protected readonly stepKey = this.selection.stepKey;

  protected onChooseTab(tab: string): void {
    this.selection.select(tab as RunnerChunkDetailTab);
  }

  /** A nav row picked in the Artifacts tab writes its key back to the URL —
   * {@link ChunkArtifactsPanel}'s viewer is a pure function of that param, never
   * its own selection state. */
  protected onSelectArtifact(key: string): void {
    this.selection.selectArtifact(key);
  }

  /** A node activated in the Node history tab writes its join key back to the
   * URL — {@link ChunkNodeHistoryTab} is a pure function of that param, never
   * its own selection state. */
  protected onSelectStep(stepKey: string | null): void {
    this.selection.selectStep(stepKey);
  }

  private readonly detailQuery = injectChunkDetailQuery(() => this.chunkId());
  private readonly workItemsQuery = injectChunkWorkItemsDetailQuery(() => this.chunkId());
  private readonly leasesQuery = injectRunnerLeasesQuery();

  protected readonly detail = computed(() => this.detailQuery.data());

  /** The Node history tab's selected step, resolved to its own artifacts — exact
   * `(node_id, epoch)` equality (`filterArtifactsByStep`), never latest-by-node, the
   * same join the hub's own `chunk-node-history-container.ts` makes over the same
   * shared `detail.artifacts`. `[]` before a step is picked or when {@link stepKey}
   * names no real join key. */
  protected readonly selectedStepArtifacts = computed<readonly ArtifactView[]>(() => {
    const key = this.stepKey();
    if (key === null) return [];
    const parsed = parseNodeStepKey(key);
    if (parsed === null) return [];
    const detail = this.detail();
    if (detail === undefined) return [];
    return sortArtifacts(filterArtifactsByStep(detail.artifacts ?? [], parsed.nodeId, parsed.epoch));
  });

  protected readonly detailState = computed<KitAsyncStateValue>(() => asyncState(this.detailQuery, false));

  /** The derived {@link Tone} the identity header's badge colors by — the same
   * `STATUS_TONE` ladder the hub's own `chunk-page.ts` reads
   * (`bzh:frontend-formatters`), since this route's `detail` is the same
   * shared `hubApi.ChunkDetail` shape field for field. */
  protected readonly tone = computed(() => STATUS_TONE[this.detail()?.status ?? 'ready']);

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
