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
  template: `
    <div class="nh-tab" data-testid="chunk-node-history-tab">
      <div class="nh-timeline" role="region" aria-labelledby="nh-timeline-heading">
        <div class="s-head"><span class="tag" id="nh-timeline-heading">Timeline</span></div>
        <fleet-chunk-detail-timeline
          [detail]="detail()"
          [heading]="false"
          [activatable]="true"
          [selectedKey]="selectedKey()"
          (pickStep)="pickStep.emit($event)"
        />
      </div>
      <section class="nh-step">
        @if (selectedKey() === null) {
          <p class="hint" data-testid="node-history-select-hint">Select a step above.</p>
        } @else {
          <div class="nh-artifacts">
            <div class="s-head"><span class="tag">Artifacts</span></div>
            @if (stepArtifacts().length === 0) {
              <p class="none" data-testid="node-history-artifacts-empty">No artifacts for this step.</p>
            } @else {
              @for (art of stepArtifacts(); track art.key) {
                <fleet-chunk-detail-artifact-body class="art" [artifact]="art" body="full" testid="node-history-artifact" />
              }
            }
          </div>
          <div class="nh-transcript">
            <div class="s-head"><span class="tag">Transcript</span></div>
            @if (isForbidden()) {
              <fleet-kit-async-state
                state="empty"
                tone="accent"
                emptyText="NO PERMISSION TO READ TRANSCRIPTS"
                emptyTestid="node-history-transcript-forbidden"
              />
            } @else if (indexState() === 'loading') {
              <fleet-kit-async-state state="loading" loadingText="LOADING…" loadingTestid="node-history-transcript-loading" />
            } @else if (indexState() === 'error') {
              <fleet-kit-async-state state="error" errorText="TRANSCRIPT UNAVAILABLE" errorTestid="node-history-transcript-error" />
            } @else if (segmentState() === 'empty') {
              <fleet-kit-async-state
                state="empty"
                emptyText="NO TRANSCRIPT FOR THIS STEP"
                emptyTestid="node-history-transcript-empty"
              />
            } @else if (segmentState() === 'loading') {
              <fleet-kit-async-state state="loading" loadingText="LOADING…" loadingTestid="node-history-transcript-loading" />
            } @else if (segmentState() === 'error') {
              <fleet-kit-async-state state="error" errorText="TRANSCRIPT UNAVAILABLE" errorTestid="node-history-transcript-error" />
            } @else {
              @if (turnsCapped()) {
                <p class="banner" data-testid="node-history-transcript-turns-capped">
                  SHOWING THE MOST RECENT {{ MAX_RENDERED_TURNS }} TURNS
                </p>
              }
              <fleet-transcript-viewer [turns]="cappedTurns()" data-testid="node-history-transcript-body" />
              @if (extraSegmentCount() > 0) {
                <p class="more" data-testid="node-history-transcript-more">
                  +{{ extraSegmentCount() }} more segment{{ extraSegmentCount() === 1 ? '' : 's' }} — open in the Transcripts tab.
                </p>
              }
            }
          </div>
        }
      </section>
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
    .nh-tab {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow-y: auto;
    }
    .nh-timeline {
      flex: none;
      border-bottom: 1px solid var(--line);
    }
    .nh-step {
      flex: 1;
      min-height: 0;
    }
    .hint {
      margin: 0;
      padding: 8px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .s-head {
      padding: 6px 8px 0;
    }
    .tag {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .none {
      margin: 0;
      padding: 4px 8px 8px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .art {
      display: block;
      padding: 4px 8px;
    }
    .nh-transcript {
      border-top: 1px solid var(--line);
    }
    .more {
      margin: 0;
      padding: 4px 8px 8px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .banner {
      margin: 0;
      padding: 4px 8px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    @media (min-width: 720px) {
      .nh-tab {
        flex-direction: row;
        overflow: hidden;
      }
      .nh-timeline {
        flex: none;
        width: 320px;
        max-height: none;
        overflow-y: auto;
        border-bottom: none;
        border-right: 1px solid var(--line);
      }
      .nh-step {
        overflow-y: auto;
      }
    }
  `,
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
