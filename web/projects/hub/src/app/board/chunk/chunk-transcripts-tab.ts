import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import {
  deriveTranscriptSteps,
  injectHubChunkTranscriptSegmentQuery,
  injectHubChunkTranscriptsQuery,
  KitAsyncState,
  type KitAsyncStateValue,
  TranscriptFetchError,
  type TranscriptSegmentIndexEntry,
  type TranscriptStep,
  TranscriptViewer,
  type TransitionView,
} from 'fleet';

/**
 * The chunk detail page's Transcripts tab (blizzard#248 Phase 2) — a nav of node-history
 * steps, each holding its segments, beside a lazily-fetched segment viewer. Mirrors
 * {@link ChunkArtifactsTab}'s nav-beside-viewer shape, but owns its own two queries
 * (blizzard#248 D8: the index on open, one segment's turns only once opened) rather than
 * reading data already on `detail()` — nothing about a chunk's transcripts is in its
 * payload (D8, pinned at `test_chunk_detail_carries_no_transcript_field`).
 *
 * The index query always fires once a chunk is selected, regardless of `transcript:read`
 * (D9): {@link ChunkPage} already hides this tab's *option* from the strip for an
 * identity without it, but a held deep link still reaches this component, and the
 * backend, not a client-side guess, is the source of truth — a 403 renders as this
 * container's own honest state rather than a generic error or a silent empty tab.
 */
@Component({
  selector: 'app-chunk-transcripts-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, TranscriptViewer],
  template: `
    @if (navState() === 'loading') {
      <fleet-kit-async-state state="loading" loadingText="LOADING TRANSCRIPTS…" loadingTestid="transcripts-loading" />
    } @else if (isForbidden()) {
      <fleet-kit-async-state
        state="empty"
        tone="accent"
        emptyText="NO PERMISSION TO READ TRANSCRIPTS"
        emptyTestid="transcripts-forbidden"
      />
    } @else if (navState() === 'error') {
      <fleet-kit-async-state state="error" errorText="TRANSCRIPTS UNAVAILABLE" errorTestid="transcripts-error" />
    } @else if (steps().length === 0) {
      <fleet-kit-async-state state="empty" emptyText="NO TRANSCRIPT SEGMENTS YET" emptyTestid="transcripts-empty" />
    } @else {
      <div class="tx-tab" data-testid="chunk-transcripts-tab">
        <nav class="tx-nav" data-testid="transcripts-tab-nav">
          @for (step of steps(); track step.key) {
            <div class="step" data-testid="transcript-step">
              <div class="step-head">
                {{ stepLabel(step) }}
                @if (step.current) {
                  <span class="tag">in progress</span>
                }
                @if (!step.matched) {
                  <span class="tag warn">unmatched</span>
                }
              </div>
              @if (step.segments.length === 0) {
                <p class="none">No segments.</p>
              } @else {
                <ul class="seg-list">
                  @for (seg of step.segments; track seg.segment_id) {
                    <li>
                      <button
                        type="button"
                        class="seg-item"
                        [class.active]="seg.segment_id === segmentId()"
                        data-testid="transcript-segment-item"
                        [attr.data-segment-id]="seg.segment_id"
                        (click)="pickSegment.emit(seg.segment_id)"
                      >
                        <span>Segment {{ seg.spawn_generation + 1 }}</span>
                        @if (seg.truncated) {
                          <span class="tag warn">truncated</span>
                        }
                        @if (!seg.final) {
                          <span class="tag">open</span>
                        }
                      </button>
                    </li>
                  }
                </ul>
              }
            </div>
          }
        </nav>
        <section class="tx-view">
          @if (segmentId() === null) {
            <fleet-kit-async-state state="empty" emptyText="SELECT A SEGMENT" emptyTestid="transcript-segment-empty" />
          } @else if (segmentQuery.isPending()) {
            <fleet-kit-async-state state="loading" loadingText="LOADING SEGMENT…" loadingTestid="transcript-segment-loading" />
          } @else if (segmentQuery.isError()) {
            <fleet-kit-async-state state="error" errorText="SEGMENT UNAVAILABLE" errorTestid="transcript-segment-error" />
          } @else {
            <div class="seg-body" data-testid="transcript-segment-body">
              @if (standaloneSidechain(); as sidechain) {
                <button
                  type="button"
                  class="back"
                  data-testid="transcript-sidechain-back"
                  (click)="pickSidechain.emit(null)"
                >
                  ← Back to segment
                </button>
                <fleet-transcript-viewer [turns]="sidechain.turns" />
              } @else {
                @if (continuedFrom(); as prev) {
                  <button
                    type="button"
                    class="seam"
                    data-testid="transcript-continued-from"
                    (click)="pickSegment.emit(prev.segment_id)"
                  >
                    ← Continued from segment {{ prev.spawn_generation + 1 }}
                  </button>
                }
                @if (segmentQuery.data()?.truncated) {
                  <p class="banner" data-testid="transcript-segment-truncated">TRUNCATED — SOME CONTENT WAS DROPPED</p>
                }
                <fleet-transcript-viewer
                  [turns]="segmentQuery.data()?.turns ?? []"
                  (openStandalone)="pickSidechain.emit('' + $event.index)"
                />
                @if (continuesIn(); as next) {
                  <button
                    type="button"
                    class="seam"
                    data-testid="transcript-continues-in"
                    (click)="pickSegment.emit(next.segment_id)"
                  >
                    Continues in segment {{ next.spawn_generation + 1 }} →
                  </button>
                }
              }
            </div>
          }
        </section>
      </div>
    }
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
    }
    .tx-tab {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
    }
    .tx-nav {
      flex: none;
      display: flex;
      flex-direction: column;
      min-height: 0;
      max-height: 40%;
      overflow-y: auto;
      border-bottom: 1px solid var(--line);
    }
    .step {
      padding: 6px 8px;
      border-bottom: 1px solid var(--line);
    }
    .step-head {
      display: flex;
      align-items: center;
      gap: 6px;
      color: var(--label);
      font-size: var(--fs-xs);
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 4px;
    }
    .tag {
      font-size: var(--fs-label);
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--label-dim);
      border: 1px solid var(--line);
      padding: 0 4px;
    }
    .tag.warn {
      color: var(--amber);
      border-color: var(--amber-dim);
    }
    .none {
      margin: 0;
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .seg-list {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .seg-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      width: 100%;
      text-align: left;
      font-family: inherit;
      background: transparent;
      border: none;
      border-left: 2px solid transparent;
      color: var(--text);
      cursor: pointer;
      padding: 4px 6px;
      font-size: var(--fs-sm);
    }
    .seg-item:hover {
      background: var(--overlay-20);
    }
    .seg-item.active {
      border-left-color: var(--cyan);
      background: color-mix(in srgb, var(--cyan) 8%, var(--overlay-25));
    }
    .tx-view {
      position: relative;
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      background: var(--overlay-25);
    }
    .seg-body {
      padding: 6px 8px;
    }
    .banner {
      color: var(--amber-hi);
      font-size: var(--fs-sm);
      letter-spacing: 0.1em;
      padding: 5px 8px;
      border-bottom: 1px solid var(--line);
      background: var(--overlay-25);
      margin: 0 0 6px;
    }
    .back,
    .seam {
      display: block;
      width: 100%;
      text-align: left;
      background: transparent;
      border: none;
      border-bottom: 1px solid var(--line);
      color: var(--cyan);
      font-family: inherit;
      font-size: var(--fs-xs);
      cursor: pointer;
      padding: 4px 0;
      margin-bottom: 4px;
    }
    @media (min-width: 720px) {
      .tx-tab {
        flex-direction: row;
      }
      .tx-nav {
        width: 300px;
        max-height: none;
        border-bottom: none;
        border-right: 1px solid var(--line);
      }
    }
  `,
})
export class ChunkTranscriptsTab {
  /** The chunk this tab is for. */
  readonly chunkId = input.required<string>();

  /** `ChunkDetail.history` — the node-history steps to group segments under. */
  readonly history = input.required<readonly TransitionView[]>();

  /** `ChunkDetail.current_node_id`/`.current_node_name`/`.latest_epoch` — the in-flight step. */
  readonly currentNodeId = input<string | null>(null);
  readonly currentNodeName = input<string | null>(null);
  readonly latestEpoch = input<number | null>(null);

  /** The `?segment` URL param — the open segment, or `null`. */
  readonly segmentId = input<string | null>(null);

  /** The `?sidechain` URL param — an unlinked sidechain's turn index, opened
   * standalone within the open segment, or `null`. */
  readonly sidechainTurnIndex = input<string | null>(null);

  /** Emitted with a segment id when the operator picks it, or `null` to close one. */
  readonly pickSegment = output<string | null>();

  /** Emitted with a turn index (as a string) when the operator opens a sidechain
   * standalone, or `null` to return to the segment. */
  readonly pickSidechain = output<string | null>();

  private readonly indexQuery = injectHubChunkTranscriptsQuery(() => this.chunkId());
  protected readonly segmentQuery = injectHubChunkTranscriptSegmentQuery(() => this.chunkId(), () => this.segmentId());

  protected readonly isForbidden = computed(() => {
    const err = this.indexQuery.error();
    return err instanceof TranscriptFetchError && err.status === 403;
  });

  protected readonly navState = computed<KitAsyncStateValue>(() => {
    if (this.indexQuery.isPending()) return 'loading';
    if (this.indexQuery.isError()) return 'error';
    return 'ready';
  });

  protected readonly steps = computed<readonly TranscriptStep[]>(() =>
    deriveTranscriptSteps(this.indexQuery.data()?.segments ?? [], this.history(), {
      nodeId: this.currentNodeId(),
      nodeName: this.currentNodeName(),
      epoch: this.latestEpoch(),
    }),
  );

  /** The open segment's own step and its index within that step's ordered segments —
   * `null` while no segment is open, or one names nothing in {@link steps}. */
  private readonly openStepAndIndex = computed<{ step: TranscriptStep; index: number } | null>(() => {
    const id = this.segmentId();
    if (id === null) return null;
    for (const step of this.steps()) {
      const index = step.segments.findIndex((s) => s.segment_id === id);
      if (index !== -1) return { step, index };
    }
    return null;
  });

  /** The resume-seam link back (blizzard#248 D6) — derived from the same ordering as
   * {@link continuesIn}, not a separate field. */
  protected readonly continuedFrom = computed<TranscriptSegmentIndexEntry | null>(() => {
    const found = this.openStepAndIndex();
    if (found === null || found.index === 0) return null;
    return found.step.segments[found.index - 1];
  });

  /** The resume-seam link forward (blizzard#248 D6). */
  protected readonly continuesIn = computed<TranscriptSegmentIndexEntry | null>(() => {
    const found = this.openStepAndIndex();
    if (found === null) return null;
    return found.step.segments[found.index + 1] ?? null;
  });

  /** The unlinked sidechain opened standalone (blizzard#248 D7), or `null` when none
   * is — the open segment's own top-level `"sidechain"` turn named by
   * {@link sidechainTurnIndex}. */
  protected readonly standaloneSidechain = computed(() => {
    const raw = this.sidechainTurnIndex();
    if (raw === null) return null;
    const index = Number(raw);
    const turn = this.segmentQuery.data()?.turns?.find((t) => t.index === index && t.kind === 'sidechain');
    return turn?.sidechain ?? null;
  });

  protected stepLabel(step: TranscriptStep): string {
    const name = step.nodeName ?? step.nodeId ?? '—';
    return step.epoch === null ? name : `${name} · epoch ${step.epoch}`;
  }
}
