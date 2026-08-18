import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import {
  deriveTranscriptSteps,
  encodeSidechainPath,
  KitAsyncState,
  type KitAsyncStateValue,
  parseSidechainPath,
  mergeLateLinks,
  resolveSegmentSeams,
  resolveSidechainByPath,
  type SidechainOpenEvent,
  type TranscriptSegmentContentView,
  type TranscriptSegmentIndexEntry,
  type TranscriptStep,
  TranscriptViewer,
  type TransitionView,
} from 'fleet';

/** Keep only the most recent this-many turns rendered for one segment — mirrors the
 * runner panel's own `MAX_TURNS` cap (`projected_transcript_repository.py`), so neither
 * surface renders an unbounded DOM for one large segment (`review:F7`). A sidechain's
 * own turns are uncapped, same as the runner side. */
const MAX_RENDERED_TURNS = 1000;

/**
 * The chunk detail page's Transcripts tab (blizzard#248 Phase 2) — a nav of node-history
 * steps, each holding its segments, beside a lazily-fetched segment viewer. Mirrors
 * {@link ChunkArtifactsTab}'s nav-beside-viewer shape and, like it, is presentational
 * (`bzh:frontend-container-presentational`, `review:F1`): the two queries behind this tab
 * (D8: the index on open, one segment's turns only once opened) live on
 * `ChunkTranscriptsContainer`, which passes their resolved state down as inputs — nothing
 * about a chunk's transcripts is in `detail()`'s own payload (D8, pinned at
 * `test_chunk_detail_carries_no_transcript_field`).
 *
 * {@link indexState}/{@link segmentState} are that container's own `asyncState()` folds over
 * its two queries (`bzh:frontend-empty-state-gated`); {@link isForbidden} is carried
 * separately since a 403 on the index read is its own honest state (D9), not a generic error.
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
                <fleet-transcript-viewer [turns]="sidechain.turns" (openStandalone)="onStandaloneOpenStandalone($event)" />
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
                <fleet-transcript-viewer [turns]="cappedTurns()" (openStandalone)="onTopLevelOpenStandalone($event)" />
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
      /* review:F2 — the four top-level \`fleet-kit-async-state\` states below render as
         direct children of this host with no wrapping element; \`.status\`'s
         \`position: absolute\` centers on the browser viewport with no positioned
         ancestor here, the way \`.tx-view\` below already provides one level down. */
      position: relative;
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
      background: var(--tint-selected);
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

  /** The `injectHubChunkTranscriptsQuery` read, resolved: the segment index once {@link indexState} is `'ready'`, `[]` otherwise. */
  readonly segments = input<readonly TranscriptSegmentIndexEntry[]>([]);

  /** `asyncState()` over the index query — never `'empty'`; "no segments yet" is this component's own {@link steps}-derived state. */
  readonly indexState = input.required<KitAsyncStateValue>();

  /** Whether the index read came back 403 (D9), checked ahead of {@link indexState}'s generic `'error'` (its own honest state). */
  readonly isForbidden = input(false);

  /** The `?segment` URL param — the open segment, or `null`. */
  readonly segmentId = input<string | null>(null);

  /** The `?sidechain` URL param, raw (`review:F3`) — a dot-joined `SidechainPath` (`fleet`'s `transcript-sidechain-path.ts`), or `null`. */
  readonly sidechainPath = input<string | null>(null);

  /** The `injectHubChunkTranscriptSegmentQuery` read: `'empty'` while {@link segmentId} names nothing, else loading/error/ready. */
  readonly segmentState = input.required<KitAsyncStateValue>();

  /** The open segment's turns and completion state, once {@link segmentState} is `'ready'`. */
  readonly segmentData = input<TranscriptSegmentContentView | undefined>(undefined);

  /** Emitted with a segment id when the operator picks it, or `null` to close one. */
  readonly pickSegment = output<string | null>();

  /** Emitted with an encoded `SidechainPath` (`review:F3`) when the operator opens a sidechain standalone, or `null` to return. */
  readonly pickSidechain = output<string | null>();

  protected readonly MAX_RENDERED_TURNS = MAX_RENDERED_TURNS;

  protected readonly steps = computed<readonly TranscriptStep[]>(() =>
    deriveTranscriptSteps(this.segments(), this.history(), {
      nodeId: this.currentNodeId(),
      nodeName: this.currentNodeName(),
      epoch: this.latestEpoch(),
    }),
  );

  /** The open segment's resume-seam links (blizzard#248 D6) — the pure derivation itself
   * lives beside {@link deriveTranscriptSteps} (`review:F11`), tested there without a
   * mounted fixture; this component only resolves it against its own {@link steps}. */
  private readonly seams = computed(() => resolveSegmentSeams(this.steps(), this.segmentId()));

  protected readonly continuedFrom = computed<TranscriptSegmentIndexEntry | null>(() => this.seams().continuedFrom);

  protected readonly continuesIn = computed<TranscriptSegmentIndexEntry | null>(() => this.seams().continuesIn);

  /** {@link segmentData}'s turns with every late link folded onto its call (blizzard#338).
   * Derived ONCE, ahead of both the cap and the standalone path resolver: merging fewer turns
   * than the path is resolved against would open the wrong sidechain. */
  private readonly mergedTurns = computed(() => mergeLateLinks(this.segmentData()?.turns ?? []));

  /** {@link mergedTurns}, tail-capped at {@link MAX_RENDERED_TURNS} the same way
   * the runner panel caps its own list (`review:F7`). A sidechain's own turns pass
   * through {@link TranscriptViewer} uncapped, same as the runner side. */
  protected readonly cappedTurns = computed(() => {
    const turns = this.mergedTurns();
    return turns.length > MAX_RENDERED_TURNS ? turns.slice(-MAX_RENDERED_TURNS) : turns;
  });

  protected readonly turnsCapped = computed(() => this.mergedTurns().length > MAX_RENDERED_TURNS);

  /** {@link sidechainPath}, parsed — `[]` when none is open. */
  private readonly parsedSidechainPath = computed(() => parseSidechainPath(this.sidechainPath()));

  /** The sidechain opened standalone (D7, `review:F3`), or `null` — walks
   * {@link parsedSidechainPath} down through every nesting level it names, not just a
   * single top-level index: a nested sidechain's own turns index independently from 0. */
  protected readonly standaloneSidechain = computed(() =>
    resolveSidechainByPath(this.mergedTurns(), this.parsedSidechainPath()),
  );

  /** A top-level "open standalone" click — the event's path is already the full address
   * from the segment's top-level turns (`review:F3`). */
  protected onTopLevelOpenStandalone(event: SidechainOpenEvent): void {
    this.pickSidechain.emit(encodeSidechainPath(event.path));
  }

  /** An "open standalone" click from *within* the standalone view (`review:F3`; this
   * binding didn't exist before, so these controls were dead) — its path is relative to
   * the already-open sidechain's own turns, so the full address prepends
   * {@link parsedSidechainPath} in front. */
  protected onStandaloneOpenStandalone(event: SidechainOpenEvent): void {
    this.pickSidechain.emit(encodeSidechainPath([...this.parsedSidechainPath(), ...event.path]));
  }

  protected stepLabel(step: TranscriptStep): string {
    const name = step.nodeName ?? step.nodeId ?? '—';
    return step.epoch === null ? name : `${name} · epoch ${step.epoch}`;
  }
}
