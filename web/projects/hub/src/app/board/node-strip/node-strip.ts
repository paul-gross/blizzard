import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { groupBlocks, isDenseStrip, type NodeBlock } from './node-strip.model';

/**
 * A chunk's progress through its graph: one block per node, start to finish, each lit by the chunk's last visit
 * there — green passed, red failed, dark unvisited — and the node it stands on pulsing in the fleet registry's
 * overload cyan. Shared by the board's card and list row, and the phone's glance.
 *
 * Presentational: it draws the hub's `ChunkSummary.graph_progress` as handed in, and nothing for an empty one.
 */
@Component({
  selector: 'app-node-strip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './node-strip.html',
  styleUrl: './node-strip.css',
  host: { '[class.dense]': 'dense()' },
})
export class NodeStrip {
  readonly progress = input.required<readonly NodeBlock[]>();

  protected readonly groups = computed(() => groupBlocks(this.progress()));

  /** Past twenty blocks the strip packs tighter, so each still reads. */
  protected readonly dense = computed(() => isDenseStrip(this.groups()));
}
