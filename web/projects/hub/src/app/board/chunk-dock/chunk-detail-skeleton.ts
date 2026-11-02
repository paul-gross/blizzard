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
  /** The work item's dependency edges: depends-on and blocks, each a few row widths (%). */
  protected readonly edgeSides = [
    [70, 55],
    [62, 78, 48],
  ];

  /** The issue pane's accordion row widths (%). */
  protected readonly issueRows = [64, 48, 72];

  /** Node-history rows: node, efficiency, verdict, runner widths (%). */
  protected readonly historyRows = [
    [70, 88, 60, 40],
    [55, 80, 75, 45],
    [80, 92, 50, 35],
    [62, 76, 68, 48],
    [74, 84, 56, 38],
    [58, 90, 72, 44],
  ];

  /** Artifact rows: name width (%) and kind width (px). */
  protected readonly artifactRows = [
    [55, 36],
    [42, 28],
    [64, 40],
    [48, 32],
    [58, 30],
  ];
}
