import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { compactRef, KitBadge, toneColor, type runnerApi } from 'fleet';

import { injectChunkTitleQuery } from './chunk-title.query';
import type { MachineChunkStatus } from './chunk-status';

/**
 * One chunk on this machine — the machine-chunks list's card (issue #134):
 * a lane-colored left edge (the derived status's own {@link toneColor}, the
 * same ladder {@link KitBadge} paints its pill with — the hub board's own
 * card scheme, `fleet/board-shell/board-shell.ts`) over three stacked lines —
 * compact chunk ref + node name/attempt epoch, the work-item chips (linked to
 * the work items) and title, then the derived status. Same fields as the
 * row this replaces, laid out like the hub board's card instead of one
 * cramped grid line.
 *
 * The work-item enrichment is the same severable, volatile layering the old lease row
 * carried (issue #28, decision 1): one {@link injectChunkTitleQuery} per row,
 * read optimistically — every degraded case (hub down, no source, per-pointer
 * forge failure) collapses to "render nothing extra". A pointer with a
 * `web_url` renders as a real link; clicking it must select nothing, so the
 * anchor stops propagation.
 */
@Component({
  selector: 'local-chunk-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  template: `
    <div
      class="c-card"
      data-testid="chunk-row"
      [attr.data-chunk-id]="chunkId()"
      [class.selected]="selected()"
      [style.border-left-color]="laneColor()"
      role="button"
      tabindex="0"
      (click)="onSelect()"
      (keydown.enter)="onSelect($event)"
      (keydown.space)="onSelect($event)"
    >
      <div class="tid">
        <span class="cid">{{ chunkRef() }}</span>
        <span class="node">{{ lease().node_name }} · a{{ lease().epoch }}</span>
      </div>
      <div class="ttl" data-testid="chunk-row-title">
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
      <div class="st-row">
        <fleet-kit-badge [tone]="status().tone" data-testid="chunk-row-status">{{ status().label }}</fleet-kit-badge>
      </div>
    </div>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .c-card {
      box-sizing: border-box;
      display: flex;
      flex-direction: column;
      gap: 3px;
      padding: 5px 8px;
      border: 1px solid var(--line);
      border-left: 3px solid transparent;
      cursor: pointer;
    }
    /* A shared 1px hairline between adjacent cards — every card keeps its own
       bottom border, but a non-first card drops its top border so the two
       don't stack into a 2px seam. */
    .c-card:not(:first-child) {
      border-top: none;
    }
    .c-card:hover {
      background: var(--panel-deep);
    }
    /* An outline ring (not border-color, which would repaint the tone-colored
       left edge) plus a faint cyan wash — the hub board card's own selected
       treatment (fleet/board-shell/board-shell.ts), so the two read alike. */
    .c-card.selected {
      outline: 1px solid var(--cyan);
      outline-offset: -1px;
      background: color-mix(in srgb, var(--cyan) 8%, var(--panel-deep));
    }
    .c-card:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: -1px;
    }
    .tid {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 8px;
      min-width: 0;
    }
    .cid {
      color: var(--amber);
      font-size: var(--fs-base);
    }
    .node {
      color: var(--label);
      font-size: var(--fs-xs);
      white-space: nowrap;
    }
    .ttl {
      color: var(--text);
      font-size: var(--fs-sm);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      min-width: 0;
    }
    .chip {
      color: var(--cyan);
      text-decoration: none;
      margin-right: 6px;
    }
    a.chip:hover {
      text-decoration: underline;
    }
    .st-row {
      display: flex;
      justify-content: flex-end;
    }
  `,
})
export class ChunkRow {
  /** The chunk's newest lease — the row's execution facts (node, epoch). */
  readonly lease = input.required<runnerApi.LeaseView>();

  /** The derived machine-side status, folded by the container (one owner). */
  readonly status = input.required<MachineChunkStatus>();

  /** Whether the container considers this row the current selection. */
  readonly selected = input(false);

  /** Emits this row's `chunk_id` on click/Enter/Space — same convention as `selectLease`. */
  readonly selectChunk = output<string>();

  protected onSelect(event?: Event): void {
    event?.preventDefault();
    this.selectChunk.emit(this.chunkId());
  }

  protected readonly chunkId = computed(() => this.lease().chunk_id);
  protected readonly chunkRef = computed(() => compactRef(this.chunkId()));

  /** The card's left edge — {@link toneColor} off the derived status, the
   * same ladder the status badge paints with, so the two never disagree. */
  protected readonly laneColor = computed(() => toneColor(this.status().tone));

  /** The severable work-item read (issue #28, decision 1) — never branched on for pending/error. */
  protected readonly titleQuery = injectChunkTitleQuery(() => this.chunkId());

  protected readonly linkedItems = computed(() => this.titleQuery.data()?.items ?? []);

  /** The first work-item's title, or empty when unresolved/failed/absent. */
  protected readonly titleText = computed<string>(() => this.titleQuery.data()?.items?.[0]?.title ?? '');
}
