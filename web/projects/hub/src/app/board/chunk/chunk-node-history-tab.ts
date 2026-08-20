import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import {
  type ArtifactView,
  ChunkArtifactBody,
  ChunkTimelineSelection,
  type hubApi,
  KitAccordionSection,
  KitAsyncState,
  type KitAsyncStateValue,
  mergeLateLinks,
  type TranscriptSegmentContentView,
  type TranscriptSegmentIndexEntry,
  TranscriptSegmentView,
} from 'fleet';

/** {@link ChunkTimelineSelection.graphLinkBase} — the graphs view's own path segments,
 * the same target every other hub composition site links a row's graph badge to. */
const GRAPH_LINK_BASE: readonly string[] = ['/graphs'];

/**
 * The chunk detail page's Node history tab — {@link ChunkTimelineSelection}'s
 * three-line rows beside the selected step's own transcript and artifacts, each in its
 * own collapsible {@link KitAccordionSection} rather than the always-visible stack this
 * tab rendered before: a step can carry more than one transcript segment (a resumed
 * lease), and every one of them pages through the same seam buttons the Transcripts tab
 * already gives — no "open in the Transcripts tab" pointer remains. Presentational
 * (`bzh:frontend-container-presentational`): {@link ChunkNodeHistoryContainer} owns the
 * transcript-index/segment queries this needs and forwards their resolved state down, the
 * same split {@link ChunkTranscriptsContainer}/`ChunkTranscriptsTab` already establish.
 *
 * D8: the join is exact `(node_id, epoch)` equality — {@link stepArtifacts} is already
 * filtered that way by the container ({@link filterArtifactsByStep}), never latest-by-node.
 *
 * D7: the artifact half rides {@link detail}, already resolved, and states its own empty
 * case directly; the transcript half is query-gated through {@link KitAsyncState} via
 * {@link indexState}/{@link segmentState} — `[]` during the first fetch is indistinguishable
 * from a settled empty read without that gate.
 *
 * Both accordion sections default open — the least behavior change from the always-visible
 * layout this replaces — and more than one can be open at once; nothing here coordinates
 * them shut. The transcript body itself renders through {@link TranscriptSegmentView}, the
 * same shared seam-button/turn-cap body the Transcripts tab mounts, so this pane's paging
 * carries no copy of that markup.
 */
@Component({
  selector: 'app-chunk-node-history-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, ChunkTimelineSelection, KitAccordionSection, KitAsyncState, TranscriptSegmentView],
  templateUrl: './chunk-node-history-tab.html',
  styleUrl: './chunk-node-history-tab.css',
})
export class ChunkNodeHistoryTab {
  readonly detail = input.required<hubApi.ChunkDetail>();

  /** The raw `?step` URL param — forwarded straight to {@link ChunkTimelineSelection} with
   * no lookup against the timeline's own rows here. */
  readonly selectedKey = input<string | null>(null);

  /** The selected step's own artifacts, already filtered by the container (D8: exact
   * `(node_id, epoch)`, never latest-by-node). */
  readonly stepArtifacts = input<readonly ArtifactView[]>([]);

  readonly indexState = input.required<KitAsyncStateValue>();
  readonly isForbidden = input(false);
  readonly segmentState = input.required<KitAsyncStateValue>();
  readonly segmentData = input<TranscriptSegmentContentView | undefined>(undefined);

  /** The effective (operator-paged or step-default) segment's own resume-seam links —
   * {@link ChunkNodeHistoryContainer.continuedFrom}/`.continuesIn` forwarded straight
   * through, the same shape {@link ChunkTranscriptsTab} already renders. */
  readonly continuedFrom = input<TranscriptSegmentIndexEntry | null>(null);
  readonly continuesIn = input<TranscriptSegmentIndexEntry | null>(null);

  /** Forwarded straight from {@link ChunkTimelineSelection.pickStep}, a row's join key
   * when the operator activates it, or `null` when they clear the selection by
   * re-activating the already-selected row. */
  readonly pickStep = output<string | null>();

  /** A transcript seam button followed — the target segment id, forwarded straight to
   * the container, which pages {@link segmentData} to it. */
  readonly pickSegment = output<string>();

  protected readonly graphLinkBase = GRAPH_LINK_BASE;

  protected readonly transcriptsExpanded = signal(true);
  protected readonly artifactsExpanded = signal(true);

  /** {@link segmentData}'s turns with every late link folded onto its call —
   * {@link TranscriptSegmentView} caps and renders it; this component only merges. */
  protected readonly mergedTurns = computed(() => mergeLateLinks(this.segmentData()?.turns ?? []));
}
