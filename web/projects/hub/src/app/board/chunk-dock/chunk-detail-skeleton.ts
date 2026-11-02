import { ChangeDetectionStrategy, Component } from '@angular/core';

import { KitSkeleton } from 'fleet';

/**
 * The chunk dock's loading stand-in, shaped like {@link ChunkDetailPanel}: the dock's own frame, a
 * header holding the id over the identity line with the action controls at its end, then the three
 * columns — the work item's fact rows, the node history's rows, and the artifacts — so the dock
 * keeps its shape while a chunk's detail loads, whether it is opening or switching chunks.
 *
 * Presentational, input-free.
 */
@Component({
  selector: 'app-chunk-detail-skeleton',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitSkeleton],
  templateUrl: './chunk-detail-skeleton.html',
  styleUrl: './chunk-detail-skeleton.css',
  host: { 'aria-busy': 'true', 'aria-label': 'Loading chunk detail', role: 'region' },
})
export class ChunkDetailSkeleton {
  /** The work-item column's fact rows — one per label/value pair the facts list carries. */
  protected readonly factRows = [0, 1, 2, 3, 4, 5];

  /** The node-history column's rows. */
  protected readonly historyRows = [0, 1, 2, 3];

  /** The artifacts column's rows. */
  protected readonly artifactRows = [0, 1, 2];
}
