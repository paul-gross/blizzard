import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type {
  ArtifactView,
  ChunkDetail,
  TranscriptSegmentContentView,
  TranscriptSegmentIndexEntry,
} from '../../api/hub';
import { ChunkArtifactBody } from '../chunk-detail/chunk-artifact-body';
import { ChunkTimelineSelection } from '../chunk-detail/chunk-timeline-selection';
import { KitAccordionSection } from '../../kit/kit-accordion-section';
import { KitAsyncState, type KitAsyncStateValue } from '../../kit/kit-async-state';
import { KitMasterDetail } from '../../kit/kit-master-detail';
import { mergeLateLinks } from '../../transcripts/merge-late-links';
import { TranscriptSegmentView } from '../../transcripts/transcript-segment-view';

/**
 * The chunk detail page's Node history tab — {@link ChunkTimelineSelection}'s
 * three-line rows beside the selected step's own transcript and artifacts, each in its
 * own collapsible {@link KitAccordionSection}: a step can carry more than one transcript
 * segment (a resumed lease), each paged through seam buttons. Presentational
 * (`bzh:frontend-container-presentational`): container, {@link ChunkNodeHistoryContainer}.
 *
 * The list/detail split is {@link KitMasterDetail}'s; this component projects only its own list content (the timeline) and detail content
 * (the transcript/artifacts accordions), and still computes {@link hasSelection} itself
 * since the shell cannot derive a selection from content it does not own.
 *
 * The join is exact `(node_id, epoch)` equality — {@link stepArtifacts} is already
 * filtered that way upstream ({@link filterArtifactsByStep}), never latest-by-node.
 *
 * The artifact half rides {@link detail}, already resolved, and states its own empty
 * case directly; the transcript half is query-gated through {@link KitAsyncState} via
 * {@link indexState}/{@link segmentState} — `[]` during the first fetch is indistinguishable
 * from a settled empty read without that gate.
 *
 * Both accordion sections default open, and more than one can be open at once; nothing
 * here coordinates them shut. The transcript body renders through
 * {@link TranscriptSegmentView}.
 */
@Component({
  selector: 'fleet-chunk-node-history-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, ChunkTimelineSelection, KitAccordionSection, KitAsyncState, KitMasterDetail, TranscriptSegmentView],
  templateUrl: './chunk-node-history-tab.html',
  styleUrl: './chunk-node-history-tab.css',
})
export class ChunkNodeHistoryTab {
  readonly detail = input.required<ChunkDetail>();

  /** The raw `?step` URL param — forwarded straight to {@link ChunkTimelineSelection} with
   * no lookup against the timeline's own rows here. */
  readonly selectedKey = input<string | null>(null);

  /** Opt-in phone drill-down presentation, forwarded straight to {@link KitMasterDetail}.
   * An unselected history renders only its timeline; a selected URL step renders only its
   * detail state. */
  readonly drilldown = input(false);

  /** The selected step's own artifacts, already filtered by the container (exact
   * `(node_id, epoch)`, never latest-by-node). */
  readonly stepArtifacts = input<readonly ArtifactView[]>([]);

  readonly indexState = input.required<KitAsyncStateValue>();
  readonly isForbidden = input(false);
  readonly segmentState = input.required<KitAsyncStateValue>();
  readonly segmentData = input<TranscriptSegmentContentView | undefined>(undefined);

  /** The effective (operator-paged or step-default) segment's own resume-seam links —
   * {@link ChunkNodeHistoryContainer.continuedFrom}/`.continuesIn` forwarded straight
   * through. */
  readonly continuedFrom = input<TranscriptSegmentIndexEntry | null>(null);
  readonly continuesIn = input<TranscriptSegmentIndexEntry | null>(null);

  /** Forwarded straight from {@link ChunkTimelineSelection.pickStep}, a row's join key
   * when the operator activates it, or `null` when they clear the selection by
   * re-activating the already-selected row, or by activating {@link KitMasterDetail}'s
   * own Back control. */
  readonly pickStep = output<string | null>();

  /** A transcript seam button followed — the target segment id to page to. */
  readonly pickSegment = output<string>();

  /** {@link ChunkTimelineSelection.graphLinkBase} — the daemon's graphs view, or `null`
   * to leave each row's graph badge unlinked where the daemon serves no graphs view. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  protected readonly hasSelection = computed(() => this.selectedKey() !== null);

  protected readonly transcriptsExpanded = signal(true);
  protected readonly artifactsExpanded = signal(true);

  /** {@link segmentData}'s turns with every late link folded onto its call —
   * {@link TranscriptSegmentView} caps and renders it; this component only merges. */
  protected readonly mergedTurns = computed(() => mergeLateLinks(this.segmentData()?.turns ?? []));
}
