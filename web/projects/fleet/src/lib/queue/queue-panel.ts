import { ChangeDetectionStrategy, Component, computed } from '@angular/core';

import type { QueuePeekEntry } from '../api/hub';
import { QueuePanelView } from './queue-view';
import { injectHubQueueQuery } from './queue.query';
import { injectGroupChunksMutation, injectReorderQueueMutation } from './queue.mutations';

/**
 * The queue-shaping panel — the operator's two controls over the ready queue,
 * the surface that shapes work rather than executes it:
 *
 * - **Prioritize**: a move-to-top action per row drives a whole-order `PUT /api/queue`
 *   composed client-side (issue #105 removed the single-move route); the next acquire honors the new order;
 * - **Group**: multi-select two or more ready chunks and merge them into the
 *   top-most selected survivor via `POST /api/chunks/{id}/group` — the survivor
 *   carries the union of PM pointers, the rest are discarded.
 *
 * A container (issue #80): it owns the queue query and both mutations, all
 * through the generated client (bzh:generated-client), and renders the
 * presentational {@link QueuePanelView}. The live-update service re-peeks on
 * `queue-changed`.
 */
@Component({
  selector: 'fleet-queue-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [QueuePanelView],
  template: `
    <fleet-queue-view [entries]="entries()" (moveToTop)="moveToTop($event)" (group)="group($event)" />
  `,
})
export class QueuePanel {
  private readonly queueQuery = injectHubQueueQuery();
  private readonly reorderMutation = injectReorderQueueMutation();
  private readonly groupMutation = injectGroupChunksMutation();

  /** The ready queue in hub order; empty until the first read resolves. */
  protected readonly entries = computed<readonly QueuePeekEntry[]>(() => this.queueQuery.data() ?? []);

  protected moveToTop(chunkId: string): void {
    this.reorderMutation.mutate({ chunkId, position: 0 });
  }

  /** `ids` is the view's current-queue-order selection (the top-most is the
   * group survivor) — the view owns the checkbox state itself, since it is
   * plain UI state, not query-derived. */
  protected group(ids: readonly string[]): void {
    if (ids.length < 2) return;
    const [survivorId, ...mergeChunkIds] = ids;
    this.groupMutation.mutate({ survivorId, mergeChunkIds });
  }
}
