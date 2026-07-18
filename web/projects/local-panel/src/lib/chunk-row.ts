import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { compactRef, KitBadge, type runnerApi } from 'fleet';

import { injectChunkTitleQuery } from './chunk-title.query';
import type { MachineChunkStatus } from './chunk-status';

/**
 * One chunk on this machine — the machine-chunks list's row: compact chunk ref,
 * node name + attempt epoch, the PM-item chips (linked to the work items) and
 * title, and the derived status right-aligned in the hub board's color scheme.
 *
 * The PM enrichment is the same severable, volatile layering the old lease row
 * carried (issue #28, decision 1): one {@link injectChunkTitleQuery} per row,
 * read optimistically — every degraded case (hub down, no source, per-pointer
 * forge failure) collapses to "render nothing extra". A pointer with a
 * `web_url` renders as a real link; clicking it must select nothing, so the
 * anchor stops propagation.
 */
@Component({
  selector: 'fleet-chunk-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  template: `
    <div
      class="c-row"
      data-testid="chunk-row"
      [attr.data-chunk-id]="chunkId()"
      [class.selected]="selected()"
      role="button"
      tabindex="0"
      (click)="onSelect()"
      (keydown.enter)="onSelect($event)"
      (keydown.space)="onSelect($event)"
    >
      <span class="cid">{{ chunkRef() }}</span>
      <span class="node">{{ lease().node_name }} · a{{ lease().epoch }}</span>
      <span class="ttl" data-testid="chunk-row-title">
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
      </span>
      <fleet-kit-badge [tone]="status().tone" data-testid="chunk-row-status">{{ status().label }}</fleet-kit-badge>
    </div>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .c-row {
      display: grid;
      grid-template-columns: 64px 92px 1fr auto;
      align-items: baseline;
      gap: 10px;
      padding: 5px 8px;
      border-bottom: 1px solid var(--line);
      border-left: 2px solid transparent;
      cursor: pointer;
    }
    .c-row:hover {
      background: var(--panel-deep);
    }
    .c-row.selected {
      background: var(--bezel-hi);
      border-left-color: var(--cyan);
    }
    .c-row:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: -1px;
    }
    .cid {
      color: var(--amber);
      font-size: var(--fs-base);
    }
    .node {
      color: var(--label);
      font-size: var(--fs-xs);
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
    /* Grid-cell layout only — the tone→color ladder itself is the kit
     * badge's own concern. */
    fleet-kit-badge {
      flex: none;
      text-align: right;
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

  /** The severable PM read (issue #28, decision 1) — never branched on for pending/error. */
  protected readonly titleQuery = injectChunkTitleQuery(() => this.chunkId());

  protected readonly linkedItems = computed(() => this.titleQuery.data()?.items ?? []);

  /** The first pm-item's title, or empty when unresolved/failed/absent. */
  protected readonly titleText = computed<string>(() => this.titleQuery.data()?.items?.[0]?.title ?? '');
}
