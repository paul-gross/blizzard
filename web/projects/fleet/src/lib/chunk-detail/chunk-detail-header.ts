import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkDetail, ChunkStatus, PauseView, PmPointerView, RouteView } from '../api/hub';
import { KitButton } from '../kit/kit-button';

/** Statuses the hub's `PauseService` refuses to pause (`ChunkNotPausable`), mirrored
 * here so the dock never offers a Pause the server would answer with a 409 (issue #46).
 * A terminal or mid-delivery chunk has no work to stop.
 *
 * `paused` is deliberately **absent**: whether a chunk is already paused is not a
 * question `status` can answer (PAUSED derives below the human-gated states), so it is
 * never asked here — see {@link ChunkDetailHeader.pause}, which owns that half by
 * reading the fact. */
const NOT_PAUSABLE = new Set<ChunkStatus>(['done', 'stopped', 'delivering']);

/**
 * The chunk detail dock's header (issue #79) — the chunk's identity in the
 * board's own vocabulary (the short name, its work item, its state, and the
 * node it sits at), plus the operator actions that hang off it: the **route +
 * Detach** control (issue #42), **Pause/Resume** (issue #46), and dismiss.
 *
 * Detach is deliberately **not** requeue — it supersedes no escalation and
 * bumps no epoch, so a `needs_human` chunk detached this way still derives
 * `needs_human` afterward (`src/blizzard/hub/domain/detach.py`); this header
 * never claims otherwise. Pause/Resume switches on the pause **fact**
 * (`ChunkDetail.pause`), never on `status` — a chunk both paused and parked
 * on a question derives `waiting_on_human`, so a status-keyed switch would
 * never offer Resume.
 *
 * Presentational only: it holds the detail input and emits `dismiss`,
 * `detach`, `pauseChunk`, and `resumeChunk` (each guarded by a `confirm()` —
 * the route-releasing and worker-killing verbs, the one browser affordance
 * this dock reaches for); the mutations those events drive live in the
 * container.
 */
@Component({
  selector: 'fleet-chunk-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  template: `
    <header class="d-head">
      <!-- The chunk's identity, in the board's own vocabulary: the short name in gold,
           the work item it serves in cyan, and its state — with the node it currently
           sits at pushed to the far right, the same shape the board cards use. -->
      <div class="d-title">
        <span class="id" data-testid="detail-id">{{ detail().chunk_id }}</span>
        <span class="d-sub">
          <!-- Each pointer links out to its PM source here in the detail — the board
               cards stay plain click targets, so the anchor lives on this view only.
               No web_url (unconfigured source) degrades to plain text, no broken link. -->
          @for (p of pointers(); track p.source + ':' + p.ref) {
            @if (p.web_url) {
              <a
                class="iss"
                data-testid="detail-pointer"
                [href]="p.web_url"
                target="_blank"
                rel="noreferrer"
                [attr.title]="p.web_url"
              >{{ p.label ?? p.source + '#' + p.ref }}</a>
            } @else {
              <span class="iss" data-testid="detail-pointer">{{ p.label ?? p.source + '#' + p.ref }}</span>
            }
          }
          <span class="st" data-testid="detail-status">{{ detail().status }}</span>
        </span>
      </div>
      <div class="d-meta">
        <!-- Who paused the chunk, read off the pause fact — the header states it because
             a paused chunk that is also parked on a question derives waiting_on_human, so
             the status cell above would never say so (issue #46). -->
        @if (pause(); as p) {
          <span class="pb" data-testid="chunk-pause-by">Paused by {{ p.by }}</span>
        }
        <!-- Pause/Resume sits beside Detach: the two are the halves of one choice —
             detach gives the claim away, pause keeps it. Unlike Detach it does not hang
             off the route, so it renders whether or not a route is live (issue #46). -->
        <div class="d-actions">
          @if (pause()) {
            <fleet-kit-button
              testid="resume-chunk"
              [ariaLabel]="'Resume chunk ' + detail().chunk_id"
              (click)="onResume()"
            >
              Resume
            </fleet-kit-button>
          } @else if (pausable()) {
            <fleet-kit-button
              testid="pause-chunk"
              [ariaLabel]="'Pause chunk ' + detail().chunk_id"
              (click)="onPause()"
            >
              Pause
            </fleet-kit-button>
          }
        </div>
        <!-- The route rides in the header beside Detach, not down in the facts, because
             it is the button's object: it names what is about to be released (issue #42).
             The work-item column states the same runner as a plain fact. -->
        @if (route(); as r) {
          <div class="d-route" data-testid="route-info">
            <span class="tag">Route</span>
            <span class="rn" data-testid="route-runner" [attr.title]="r.runner_id">{{ r.runner_id }}</span>
            <fleet-kit-button
              variant="danger"
              testid="detach-chunk"
              [ariaLabel]="'Detach chunk ' + detail().chunk_id + ' from its runner'"
              (click)="onDetach()"
            >
              Detach
            </fleet-kit-button>
          </div>
        }
        <span class="nd" data-testid="detail-node" [attr.title]="detail().current_node_id">{{
          detail().current_node_name ?? detail().current_node_id ?? '—'
        }}</span>
        <button type="button" class="close" data-testid="detail-close" aria-label="Close" (click)="dismiss.emit()">
          ✕
        </button>
      </div>
    </header>
  `,
  styles: `
    :host {
      display: contents;
    }
    .tag {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .d-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 6px 8px;
      border-bottom: 1px solid var(--line);
      background: var(--overlay-25);
      flex: none;
    }
    .d-title {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .d-title .id {
      color: var(--amber);
      font-size: var(--fs-lg);
      letter-spacing: 0.04em;
    }
    .d-sub {
      display: flex;
      align-items: baseline;
      gap: 6px;
      min-width: 0;
    }
    .d-sub .iss {
      color: var(--cyan);
      font-size: var(--fs-sm);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    a.iss {
      text-decoration: none;
    }
    a.iss:hover,
    a.iss:focus-visible {
      text-decoration: underline;
      outline: none;
    }
    .d-sub .st {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .d-meta {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: none;
    }
    .d-meta .nd {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.12em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    /* The pause marker reads amber — an operator-held state, not a fault. */
    .d-meta .pb {
      color: var(--amber-hi);
      font-size: var(--fs-xs);
      white-space: nowrap;
    }
    .d-route,
    .d-actions {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .d-route .rn {
      color: var(--cyan);
      font-size: var(--fs-xs);
      max-width: 12ch;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .close {
      background: transparent;
      border: 1px solid var(--line);
      color: var(--label-dim);
      cursor: pointer;
      font-family: inherit;
      padding: 2px 6px;
    }
    .close:hover {
      color: var(--text);
    }
  `,
})
export class ChunkDetailHeader {
  /** The chunk aggregate to render (identity, status, current node, pause, route). */
  readonly detail = input.required<ChunkDetail>();

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  /** Emitted with the chunk id when the operator confirms Detach (issue #42). */
  readonly detach = output<string>();

  /** Emitted with the chunk id when the operator confirms Pause (issue #46). */
  readonly pauseChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Resume (issue #46). */
  readonly resumeChunk = output<string>();

  /** The chunk's PM pointers, for the header — each linked out to its source's web
   * address when the configured binding rendered one (a null `web_url` degrades to
   * plain text, no broken link). */
  protected readonly pointers = computed<readonly PmPointerView[]>(() => this.detail().pm_pointers ?? []);

  /** The chunk's open operator pause, if any — who set it (issue #46). Read off the
   * detail's `pause` fact, not `status`: a chunk both paused and parked on a question
   * derives `waiting_on_human`, so `status` alone would never surface it.
   *
   * This is also the **Pause/Resume switch**: non-null renders Resume, null renders
   * Pause (subject to {@link pausable}). `status` must never gate Resume. */
  protected readonly pause = computed<PauseView | null>(() => this.detail().pause ?? null);

  /** Whether an **unpaused** chunk may be paused — mirrors the hub `PauseService`'s
   * refusal (`ChunkNotPausable`) so the dock never offers a control the server would
   * answer with a 409 (issue #46), exactly as Detach shows only with a live route to
   * release (issue #42). `waiting_on_human`/`needs_human` are deliberately pausable. */
  protected readonly pausable = computed<boolean>(() => !NOT_PAUSABLE.has(this.detail().status));

  /** The chunk's live route, if any — Detach shows only while this is non-null
   * (issue #42): a chunk with no live route has nothing to release. */
  protected readonly route = computed<RouteView | null>(() => this.detail().route ?? null);

  /** Confirm, then emit `detach` for the container's mutation to fire. */
  protected onDetach(): void {
    if (!this.route()) return;
    const confirmed = globalThis.confirm(
      `Detach chunk ${this.detail().chunk_id} from its runner? This releases the runner; ` +
        `the chunk keeps its current status (this is not requeue).`,
    );
    if (!confirmed) return;
    this.detach.emit(this.detail().chunk_id);
  }

  /** Confirm, then emit `pauseChunk` for the container's mutation to fire (issue #46). */
  protected onPause(): void {
    if (this.pause() || !this.pausable()) return;
    const confirmed = globalThis.confirm(
      `Pause chunk ${this.detail().chunk_id}? This kills its active worker but keeps the ` +
        `claim (this is not detach); resume it later to pick the work back up.`,
    );
    if (!confirmed) return;
    this.pauseChunk.emit(this.detail().chunk_id);
  }

  /** Confirm, then emit `resumeChunk` for the container's mutation to fire (issue #46).
   * Guarded on the pause **fact**, never on `status`. */
  protected onResume(): void {
    if (!this.pause()) return;
    const confirmed = globalThis.confirm(
      `Resume chunk ${this.detail().chunk_id}? Its runner picks the work back up from ` +
        `where the pause stopped it.`,
    );
    if (!confirmed) return;
    this.resumeChunk.emit(this.detail().chunk_id);
  }
}
