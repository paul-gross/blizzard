import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import {
  type ArtifactView,
  asyncState,
  ChunkArtifactsPanel,
  ChunkGeneralTab,
  ChunkPageHeader,
  ChunkPageShell,
  ChunkTranscriptsContainer,
  deriveWorkItemsState,
  filterArtifactsByStep,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBackBar,
  KitTabs,
  type KitTabOption,
  parseNodeStepKey,
  runnerClient,
  sortArtifacts,
  STATUS_TONE,
  type WorkItemsState,
} from 'fleet';
import { injectChunkDetailQuery, injectChunkWorkItemsDetailQuery } from 'local-panel';

import { type RunnerChunkDetailTab, injectChunkDetailSelection } from './chunk-detail-selection';
import { ChunkNodeHistoryTab } from './chunk-node-history-tab';

/**
 * The `/board/chunk/:chunkId` route (now tabbed, further widened for Node
 * history, then again for node-grouped transcripts) — the runner-local chunk detail
 * page: work item, issues, node history, asks · decisions, artifacts, and the
 * node-grouped transcript (runner-node-grouped-transcripts, replacing the prior
 * per-attempt `MachineDetail` panel), split across four tabs — General, Node history,
 * Artifacts, Transcripts — selected through {@link injectChunkDetailSelection} (`?tab=`),
 * the same shape the hub's own `chunk-page.ts` gives its own tab strip.
 *
 * A container mapping its reads down to presentational children
 * (`bzh:frontend-container-presentational`): {@link injectChunkDetailQuery}
 * for the whole aggregate ({@link ChunkGeneralTab} and siblings all declare
 * `detail: hubApi.ChunkDetail` — the runner's proxy declares that same shared
 * model, so the payload is that type field for field, escalation included,
 * and no runner-local wrapper type is owed), and {@link injectChunkWorkItemsDetailQuery}
 * for the full-fidelity work-item read ({@link ChunkGeneralTab}'s
 * `WorkItemsState` triad — deliberately not the severable
 * `injectChunkTitleQuery` the board's list rows use, since this section
 * renders a real error state rather than silently dropping one). The
 * Transcripts tab owns its own two queries entirely — {@link ChunkTranscriptsContainer},
 * mounted with this runner's own `runnerClient` (D5), the same shared component the
 * hub mounts with its own client. The chunk id rides the URL's path (`:chunkId`).
 * `?tab=`, `?artifact=`, `?step=`, `?segment=`, and `?sidechain=` are independent params
 * on the same URL: every write through {@link injectChunkDetailSelection} merges rather
 * than replaces, so switching tabs never drops another tab's own selection.
 * General and Node history each move their own layout into a presentational sibling
 * ({@link ChunkGeneralTab}, {@link ChunkNodeHistoryTab}) — the same split the hub's tabs
 * make; Artifacts stays inline since it is already one `fleet` component
 * ({@link ChunkArtifactsPanel}), nothing this container would gain from a further
 * extraction. Together this is what keeps this container under `web:lint`'s line cap.
 *
 * The Node history tab stops at each row's own artifacts — it does not wire a
 * per-step transcript the way the hub's own tab does: the hub's transcript
 * routes are declared `dependencies=[Depends(reject_runner_principal)]`, so a
 * runner-authenticated bearer is structurally refused there. The runner's own
 * chunk-scoped transcript segment routes carry no such guard (they are local reads),
 * which is what makes {@link ChunkTranscriptsContainer} reusable here at all.
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
    ChunkTranscriptsContainer,
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

  /** The plane seam {@link ChunkTranscriptsContainer} crosses (D5) — exposed as an
   * instance field so the template can bind it; a plain module import is not itself a
   * template expression. */
  protected readonly runnerClient = runnerClient;

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

  protected readonly tabOptions = TAB_OPTIONS;

  private readonly selection = injectChunkDetailSelection();

  protected readonly tab = this.selection.tab;

  /** The raw `?artifact=` param — forwarded to {@link ChunkArtifactsPanel} with no
   * lookup against the artifact store here. */
  protected readonly artifactKey = this.selection.artifactKey;

  /** The raw `?step=` param — forwarded to {@link ChunkNodeHistoryTab} with no lookup
   * against the timeline's own rows here. */
  protected readonly stepKey = this.selection.stepKey;

  /** The raw `?segment=` param — forwarded to {@link ChunkTranscriptsContainer}, a pure
   * function of that param, never its own selection state (mirrors the hub's own
   * `chunk-page.ts`). */
  protected readonly segmentId = this.selection.transcriptSegment;

  /** The raw `?sidechain=` param — see {@link segmentId}'s own doc. */
  protected readonly sidechainPath = this.selection.transcriptSidechain;

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

  /** A segment picked in the Transcripts tab writes its id back to the URL —
   * {@link ChunkTranscriptsContainer} forwards it straight to the presentational tab, a
   * pure function of that param, never its own selection state. */
  protected onSelectTranscriptSegment(segmentId: string | null): void {
    this.selection.selectTranscriptSegment(segmentId);
  }

  /** A sidechain opened standalone in the Transcripts tab writes its encoded
   * `SidechainPath` back to the URL, so it is deep-linkable. */
  protected onSelectTranscriptSidechain(path: string | null): void {
    this.selection.selectTranscriptSidechain(path);
  }

  private readonly detailQuery = injectChunkDetailQuery(() => this.chunkId());
  private readonly workItemsQuery = injectChunkWorkItemsDetailQuery(() => this.chunkId());

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

  /** The back link's query params — restores `/board`'s own `?chunk=`
   * selection (`panel-selection.ts`) to the chunk this page had open, rather
   * than landing back on the board with nothing selected. */
  protected readonly backQueryParams = computed(() => ({ chunk: this.chunkId() }));
}
