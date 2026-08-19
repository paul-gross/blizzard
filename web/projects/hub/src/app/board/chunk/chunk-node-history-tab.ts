import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import {
  type ArtifactView,
  ChunkArtifactBody,
  ChunkTimeline,
  type hubApi,
  KitAsyncState,
  type KitAsyncStateValue,
  mergeLateLinks,
  type TranscriptSegmentContentView,
  TranscriptViewer,
} from 'fleet';

/** Same tail-cap the sibling Transcripts tab applies (`chunk-transcripts-tab.ts`'s own
 * `review:F7` fix) — an uncapped turn list risks a hung tab. */
const MAX_RENDERED_TURNS = 1000;

/**
 * The chunk detail page's Node history tab (blizzard#319) — {@link ChunkTimeline} with
 * row activation on, beside the selected row's own artifacts and transcript. Presentational
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
 */
@Component({
  selector: 'app-chunk-node-history-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, ChunkTimeline, KitAsyncState, TranscriptViewer],
  templateUrl: './chunk-node-history-tab.html',
  styleUrl: './chunk-node-history-tab.css',
})
export class ChunkNodeHistoryTab {
  readonly detail = input.required<hubApi.ChunkDetail>();

  /** The raw `?step` URL param — forwarded straight to {@link ChunkTimeline} with no
   * lookup against the timeline's own rows here. */
  readonly selectedKey = input<string | null>(null);

  /** The selected step's own artifacts, already filtered by the container (D8: exact
   * `(node_id, epoch)`, never latest-by-node). */
  readonly stepArtifacts = input<readonly ArtifactView[]>([]);

  readonly indexState = input.required<KitAsyncStateValue>();
  readonly isForbidden = input(false);
  readonly segmentState = input.required<KitAsyncStateValue>();
  readonly segmentData = input<TranscriptSegmentContentView | undefined>(undefined);

  /** How many of the selected step's own segments the container did not fetch — 0 for
   * the common single-segment case. */
  readonly extraSegmentCount = input(0);

  /** Forwarded straight from {@link ChunkTimeline.pickStep} — a row's join key when the
   * operator activates it, or `null` when they clear the selection by re-activating the
   * already-selected row. */
  readonly pickStep = output<string | null>();

  protected readonly MAX_RENDERED_TURNS = MAX_RENDERED_TURNS;

  private readonly mergedTurns = computed(() => mergeLateLinks(this.segmentData()?.turns ?? []));

  /** {@link mergedTurns}, tail-capped at {@link MAX_RENDERED_TURNS} the same way
   * `chunk-transcripts-tab.ts`'s `cappedTurns` is. */
  protected readonly cappedTurns = computed(() => {
    const turns = this.mergedTurns();
    return turns.length > MAX_RENDERED_TURNS ? turns.slice(-MAX_RENDERED_TURNS) : turns;
  });

  protected readonly turnsCapped = computed(() => this.mergedTurns().length > MAX_RENDERED_TURNS);
}
