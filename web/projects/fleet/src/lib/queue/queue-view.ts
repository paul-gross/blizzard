import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type { QueuePeekEntry } from '../api/hub';
import { KitButton } from '../kit/kit-button';
import { KitPanel } from '../kit/kit-panel';

/**
 * The queue-shaping panel's presentational half (issue #80) — the row list,
 * the move-to-top button per row, and the multi-select checkboxes that feed
 * Group. Owns the checkbox **selection** itself: it is plain UI state, not
 * query-derived, so keeping it here (rather than threading it through the
 * container) is what makes this component testable by plain inputs alone.
 * Injects no query or mutation.
 */
@Component({
  selector: 'fleet-queue-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitPanel],
  template: `
    <fleet-kit-panel class="queue-panel" aria-label="Ready queue" data-testid="queue-panel" label="Ready queue · prioritize + group">
      <fleet-kit-button
        header
        variant="primary"
        testid="group-selected"
        [disabled]="selectedIds().length < 2"
        (click)="groupSelected()"
      >
        Group ({{ selectedIds().length }})
      </fleet-kit-button>
      @if (entries().length === 0) {
        <p class="none" data-testid="queue-empty">Ready queue is empty.</p>
      } @else {
        <ol class="rows" data-testid="queue-rows">
          @for (entry of entries(); track entry.chunk_id) {
            <li class="row" data-testid="queue-row" [attr.data-chunk]="entry.chunk_id">
              <input
                type="checkbox"
                class="sel"
                data-testid="queue-select"
                [attr.aria-label]="'Select ' + entry.chunk_id + ' for grouping'"
                [checked]="isSelected(entry.chunk_id)"
                (change)="toggle(entry.chunk_id)"
              />
              <span class="pos" data-testid="queue-position">{{ entry.position }}</span>
              <span class="qid" data-testid="queue-chunk-id">{{ shortId(entry.chunk_id) }}</span>
              <span class="ptr" data-testid="queue-pointer">{{ pointerLabel(entry) }}</span>
              <fleet-kit-button
                testid="queue-move-top"
                [ariaLabel]="'Move ' + entry.chunk_id + ' to top'"
                [disabled]="entry.position === 0"
                (click)="moveToTop.emit(entry.chunk_id)"
              >
                Top
              </fleet-kit-button>
            </li>
          }
        </ol>
      }
    </fleet-kit-panel>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
      color: var(--text);
    }
    .none {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      padding: 6px 8px;
    }
    .rows {
      list-style: none;
      margin: 0;
      padding: 4px;
      display: flex;
      flex-direction: column;
      gap: 3px;
      overflow-y: auto;
    }
    .row {
      display: grid;
      grid-template-columns: auto auto 1fr 1fr auto;
      align-items: center;
      gap: 8px;
      padding: 3px 6px;
      border: 1px solid var(--line);
      background: var(--overlay-20);
    }
    .pos {
      color: var(--label-dim);
      font-size: var(--fs-sm);
      min-width: 1.5em;
      text-align: right;
    }
    .qid {
      color: var(--cyan);
      font-size: var(--fs-sm);
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .ptr {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      overflow: hidden;
      text-overflow: ellipsis;
    }
  `,
})
export class QueuePanelView {
  /** The ready queue in hub order. */
  readonly entries = input.required<readonly QueuePeekEntry[]>();

  /** Emitted with a chunk id to move to the top of the queue. */
  readonly moveToTop = output<string>();

  /** Emitted with the selected chunk ids, in current queue order (the
   * top-most is the group survivor), when the operator activates Group. */
  readonly group = output<readonly string[]>();

  /** Chunk ids checked for grouping. */
  private readonly selection = signal<ReadonlySet<string>>(new Set());

  /** Selected ids in current queue order (the top-most is the group survivor). */
  protected readonly selectedIds = computed<readonly string[]>(() => {
    const sel = this.selection();
    return this.entries()
      .map((entry) => entry.chunk_id)
      .filter((id) => sel.has(id));
  });

  protected isSelected(chunkId: string): boolean {
    return this.selection().has(chunkId);
  }

  protected toggle(chunkId: string): void {
    this.selection.update((prev) => {
      const next = new Set(prev);
      if (next.has(chunkId)) next.delete(chunkId);
      else next.add(chunkId);
      return next;
    });
  }

  protected groupSelected(): void {
    const ids = this.selectedIds();
    if (ids.length < 2) return;
    this.group.emit(ids);
    this.selection.set(new Set());
  }

  protected shortId(chunkId: string): string {
    return chunkId.slice(0, 12);
  }

  protected pointerLabel(entry: QueuePeekEntry): string {
    const pointers = entry.pm_pointers ?? [];
    if (pointers.length === 0) return '—';
    const [first] = pointers;
    const suffix = pointers.length > 1 ? ` +${pointers.length - 1}` : '';
    return `${first.source}#${first.ref}${suffix}`;
  }
}
