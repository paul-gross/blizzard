import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { compactRef, KitBadge, type runnerApi } from 'fleet';

import { injectChunkTitleQuery } from './chunk-title.query';
import type { MachineChunkStatus } from './chunk-status';

/**
 * One chunk on this machine — the mobile stack's stacked-block counterpart to
 * {@link ChunkRow}'s single-line desktop grid (`bzh:frontend-kit`'s "adaptive
 * shells over shared guts"): the same inputs, the same per-row
 * {@link injectChunkTitleQuery} PM enrichment, and the same select
 * output/keyboard affordances, laid out as three lines instead of four
 * cramped columns — a 92px node cell and an ellipsized title read as
 * illegibly cramped at a 390px viewport.
 *
 * Line 1 is the compact ref plus the derived status as a soft pill
 * (mock screen C's pill vocabulary,
 * `../../../docs/designs/mobile/core-flows.html`), right-aligned. Line 2 is
 * the PM-item chips inline with the title, wrapped to two lines rather than
 * ellipsized — a mobile card has the vertical room a desktop row doesn't.
 * Line 3 is the node + attempt epoch, in the same quiet label tone
 * {@link ChunkRow}'s own `.node` cell uses.
 *
 * A tap here is inert in this chunk (`LocalPanelMobile` binds neither
 * `selectChunk` nor a `selected` state today, same as {@link ChunkRow} in the
 * mobile shell) — the output/keyboard handling stays wired so the card is
 * ready the moment a mobile detail dock exists, without a second pass here.
 */
@Component({
  selector: 'local-chunk-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  template: `
    <div
      class="c-card"
      data-testid="local-chunk-card"
      [attr.data-chunk-id]="chunkId()"
      [class.selected]="selected()"
      role="button"
      tabindex="0"
      (click)="onSelect()"
      (keydown.enter)="onSelect($event)"
      (keydown.space)="onSelect($event)"
    >
      <div class="line1">
        <span class="cid">{{ chunkRef() }}</span>
        <fleet-kit-badge
          class="pill"
          [tone]="status().tone"
          variant="soft"
          data-testid="local-chunk-card-status"
        >
          {{ status().label }}
        </fleet-kit-badge>
      </div>
      <div class="line2" data-testid="local-chunk-card-title">
        @for (item of linkedItems(); track item.ref) {
          @if (item.web_url) {
            <a class="chip" [href]="item.web_url" target="_blank" rel="noopener" (click)="$event.stopPropagation()">{{
              item.label
            }}</a>
          } @else if (item.label) {
            <span class="chip">{{ item.label }}</span>
          }
        }
        {{ titleText() }}
      </div>
      <div class="line3" data-testid="local-chunk-card-node">{{ lease().node_name }} · a{{ lease().epoch }}</div>
    </div>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .c-card {
      display: flex;
      flex-direction: column;
      gap: 4px;
      padding: 10px 12px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-left: 2px solid transparent;
      cursor: pointer;
    }
    .c-card:hover {
      background: var(--panel-deep);
    }
    .c-card.selected {
      background: var(--bezel-hi);
      border-left-color: var(--cyan);
    }
    .c-card:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: -1px;
    }
    .line1 {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
    }
    .cid {
      color: var(--amber);
      font-size: var(--fs-base);
    }
    .pill {
      flex: none;
    }
    .line2 {
      color: var(--text);
      font-size: var(--fs-sm);
      overflow-wrap: anywhere;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .chip {
      color: var(--cyan);
      text-decoration: none;
      margin-right: 6px;
    }
    a.chip:hover {
      text-decoration: underline;
    }
    .line3 {
      color: var(--label);
      font-size: var(--fs-xs);
    }
  `,
})
export class ChunkCard {
  /** The chunk's newest lease — the card's execution facts (node, epoch). */
  readonly lease = input.required<runnerApi.LeaseView>();

  /** The derived machine-side status, folded by the container (one owner). */
  readonly status = input.required<MachineChunkStatus>();

  /** Whether the container considers this card the current selection. */
  readonly selected = input(false);

  /** Emits this card's `chunk_id` on click/Enter/Space — same convention as `ChunkRow`'s `selectChunk`. */
  readonly selectChunk = output<string>();

  protected onSelect(event?: Event): void {
    event?.preventDefault();
    this.selectChunk.emit(this.chunkId());
  }

  protected readonly chunkId = computed(() => this.lease().chunk_id);
  protected readonly chunkRef = computed(() => compactRef(this.chunkId()));

  /** The severable PM read (issue #28, decision 1) — never branched on for pending/error. */
  protected readonly titleQuery = injectChunkTitleQuery(() => this.chunkId());

  protected readonly linkedItems = computed(() => this.titleQuery.data()?.items ?? []);

  /** The first pm-item's title, or empty when unresolved/failed/absent. */
  protected readonly titleText = computed<string>(() => this.titleQuery.data()?.items?.[0]?.title ?? '');
}
