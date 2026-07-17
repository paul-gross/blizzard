import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';

import type { QueuePeekEntry } from '../api/hub';
import { injectHubQueueQuery } from './queue.query';
import { injectGroupChunksMutation, injectReorderQueueMutation } from './queue.mutations';

/**
 * The queue-shaping panel (D-048) — the operator's two controls over the ready queue,
 * the surface that shapes work rather than executes it:
 *
 * - **Prioritize**: a move-to-top action per row drives `POST /api/queue/reorder`
 *   with `position: 0`; the next acquire honors the new order;
 * - **Group**: multi-select two or more ready chunks and merge them into the
 *   top-most selected survivor via `POST /api/chunks/{id}/group` — the survivor
 *   carries the union of PM pointers, the rest are discarded (D-047).
 *
 * A container: it owns the queue query and both mutations, all through the generated
 * client (bzh:generated-client); the live-update service re-peeks on `queue-changed`.
 */
@Component({
  selector: 'fleet-queue-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="panel queue-panel" aria-label="Ready queue" data-testid="queue-panel">
      <div class="panel-head">
        <span class="lbl">Ready queue · prioritize + group</span>
        <button
          type="button"
          class="act primary"
          data-testid="group-selected"
          [disabled]="selectedIds().length < 2"
          (click)="groupSelected()"
        >
          Group ({{ selectedIds().length }})
        </button>
      </div>
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
              <button
                type="button"
                class="act"
                data-testid="queue-move-top"
                [attr.aria-label]="'Move ' + entry.chunk_id + ' to top'"
                [disabled]="entry.position === 0"
                (click)="moveToTop(entry.chunk_id)"
              >
                Top
              </button>
            </li>
          }
        </ol>
      }
    </section>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
      color: var(--text);
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .panel {
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border: 1px solid var(--bezel);
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.25);
    }
    .act {
      font-family: inherit;
      background: rgba(0, 0, 0, 0.3);
      border: 1px solid var(--line);
      color: var(--text);
      cursor: pointer;
      padding: 2px 7px;
      font-size: var(--fs-xs);
    }
    .act.primary {
      color: var(--cyan);
    }
    .act:hover:not(:disabled) {
      border-color: var(--cyan);
    }
    .act:disabled {
      opacity: 0.4;
      cursor: default;
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
      background: rgba(0, 0, 0, 0.2);
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
export class QueuePanel {
  private readonly queueQuery = injectHubQueueQuery();
  private readonly reorderMutation = injectReorderQueueMutation();
  private readonly groupMutation = injectGroupChunksMutation();

  /** The ready queue in hub order (D-048); empty until the first read resolves. */
  protected readonly entries = computed<readonly QueuePeekEntry[]>(() => this.queueQuery.data() ?? []);

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

  protected moveToTop(chunkId: string): void {
    this.reorderMutation.mutate({ chunkId, position: 0 });
  }

  protected groupSelected(): void {
    const ids = this.selectedIds();
    if (ids.length < 2) return;
    const [survivorId, ...mergeChunkIds] = ids;
    this.groupMutation.mutate({ survivorId, mergeChunkIds });
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
