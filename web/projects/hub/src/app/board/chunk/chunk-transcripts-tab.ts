import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import {
  deriveTranscriptSteps,
  KitAsyncState,
  type KitAsyncStateValue,
  type TranscriptSegmentContentView,
  type TranscriptSegmentIndexEntry,
  type TranscriptStep,
  TranscriptViewer,
  type TransitionView,
} from 'fleet';

/** Keep only the most recent this-many turns rendered for one segment — mirrors the
 * runner panel's own `MAX_TURNS` cap
 * (`src/blizzard/runner/transcripts/internal/projected_transcript_repository.py`), so
 * neither surface renders an unbounded DOM for one large segment (`review:F7`). A
 * sidechain's own turns are uncapped, same as the runner side. */
const MAX_RENDERED_TURNS = 1000;

/**
 * The chunk detail page's Transcripts tab (blizzard#248 Phase 2) — a nav of node-history
 * steps, each holding its segments, beside a lazily-fetched segment viewer. Mirrors
 * {@link ChunkArtifactsTab}'s nav-beside-viewer shape and, like it, is presentational
 * (`bzh:frontend-container-presentational`, `review:F1`): the two queries behind this tab
 * (blizzard#248 D8: the index on open, one segment's turns only once opened) live on
 * {@link ChunkPage}, which passes their resolved state down as inputs — nothing about a
 * chunk's transcripts is in `detail()`'s own payload (D8, pinned at
 * `test_chunk_detail_carries_no_transcript_field`).
 *
 * {@link indexState}/{@link segmentState} are `ChunkPage`'s own `asyncState()` folds over
 * its two queries (`bzh:frontend-empty-state-gated`); {@link isForbidden} is carried
 * separately since a 403 on the index read is its own honest state (D9), not the generic
 * error `indexState` reports for anything else.
 */
@Component({
  selector: 'app-chunk-transcripts-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, TranscriptViewer],
  template: `
    @if (indexState() === 'loading') {
      <fleet-kit-async-state state="loading" loadingText="LOADING TRANSCRIPTS…" loadingTestid="transcripts-loading" />
    } @else if (isForbidden()) {
      <fleet-kit-async-state
        state="empty"
        tone="accent"
        emptyText="NO PERMISSION TO READ TRANSCRIPTS"
        emptyTestid="transcripts-forbidden"
      />
    } @else if (indexState() === 'error') {
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
          @if (segmentState() === 'empty') {
            <fleet-kit-async-state state="empty" emptyText="SELECT A SEGMENT" emptyTestid="transcript-segment-empty" />
          } @else if (segmentState() === 'loading') {
            <fleet-kit-async-state state="loading" loadingText="LOADING SEGMENT…" loadingTestid="transcript-segment-loading" />
          } @else if (segmentState() === 'error') {
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
                @if (segmentData()?.truncated) {
                  <p class="banner" data-testid="transcript-segment-truncated">TRUNCATED — SOME CONTENT WAS DROPPED</p>
                }
                @if (turnsCapped()) {
                  <p class="banner" data-testid="transcript-segment-turns-capped">
                    SHOWING THE MOST RECENT {{ MAX_RENDERED_TURNS }} TURNS
                  </p>
                }
                <fleet-transcript-viewer
                  [turns]="cappedTurns()"
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
  /** `ChunkDetail.history` — the node-history steps to group segments under. */
  readonly history = input.required<readonly TransitionView[]>();

  /** `ChunkDetail.current_node_id`/`.current_node_name`/`.latest_epoch` — the in-flight step. */
  readonly currentNodeId = input<string | null>(null);
  readonly currentNodeName = input<string | null>(null);
  readonly latestEpoch = input<number | null>(null);

  /** `ChunkPage`'s `injectHubChunkTranscriptsQuery` read, resolved: the segment index
   * once {@link indexState} is `'ready'`, `[]` otherwise. */
  readonly segments = input<readonly TranscriptSegmentIndexEntry[]>([]);

  /** `asyncState()` over `ChunkPage`'s index query (loading/error/ready — never `'empty'`;
   * "no segments yet" is this component's own {@link steps}-derived state). */
  readonly indexState = input.required<KitAsyncStateValue>();

  /** Whether the index read came back 403 (D9) — checked ahead of {@link indexState}'s
   * generic `'error'` so a permission denial renders as its own honest state. */
  readonly isForbidden = input(false);

  /** The `?segment` URL param — the open segment, or `null`. */
  readonly segmentId = input<string | null>(null);

  /** The `?sidechain` URL param — a sidechain's own turn index, opened standalone within
   * the open segment, or `null`. */
  readonly sidechainTurnIndex = input<string | null>(null);

  /** `ChunkPage`'s `injectHubChunkTranscriptSegmentQuery` read, resolved: `'empty'` while
   * {@link segmentId} names nothing (the query's own `enabled: false` rest state,
   * `bzh:frontend-empty-state-gated`'s documented trap), else loading/error/ready. */
  readonly segmentState = input.required<KitAsyncStateValue>();

  /** The open segment's turns and completion state, once {@link segmentState} is `'ready'`. */
  readonly segmentData = input<TranscriptSegmentContentView | undefined>(undefined);

  /** Emitted with a segment id when the operator picks it, or `null` to close one. */
  readonly pickSegment = output<string | null>();

  /** Emitted with a turn index (as a string) when the operator opens a sidechain
   * standalone, or `null` to return to the segment. */
  readonly pickSidechain = output<string | null>();

  protected readonly MAX_RENDERED_TURNS = MAX_RENDERED_TURNS;

  protected readonly steps = computed<readonly TranscriptStep[]>(() =>
    deriveTranscriptSteps(this.segments(), this.history(), {
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

  /** {@link segmentData}'s turns, tail-capped at {@link MAX_RENDERED_TURNS} the same way
   * the runner panel caps its own top-level list (`review:F7`) — never an unbounded DOM
   * for one large segment. A sidechain's own turns pass through {@link TranscriptViewer}
   * uncapped, same as the runner side. */
  protected readonly cappedTurns = computed(() => {
    const turns = this.segmentData()?.turns ?? [];
    return turns.length > MAX_RENDERED_TURNS ? turns.slice(-MAX_RENDERED_TURNS) : turns;
  });

  protected readonly turnsCapped = computed(() => (this.segmentData()?.turns?.length ?? 0) > MAX_RENDERED_TURNS);

  /** The sidechain opened standalone (blizzard#248 D7, `review:F3`), or `null` when none
   * is — the open segment's own top-level turn named by {@link sidechainTurnIndex},
   * whether it carries the sidechain nested (a `"tool"` turn) or unlinked (a
   * `"sidechain"` turn); both shapes carry a non-null `sidechain`. */
  protected readonly standaloneSidechain = computed(() => {
    const raw = this.sidechainTurnIndex();
    if (raw === null) return null;
    const index = Number(raw);
    const turn = this.segmentData()?.turns?.find((t) => t.index === index && t.sidechain !== null);
    return turn?.sidechain ?? null;
  });

  protected stepLabel(step: TranscriptStep): string {
    const name = step.nodeName ?? step.nodeId ?? '—';
    return step.epoch === null ? name : `${name} · epoch ${step.epoch}`;
  }
}
