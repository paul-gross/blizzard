import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { LaidOutMigration } from './graph-layout';

/** The migration pill's corner radius — rounded enough to read as a pill next to the
 * node box's mostly-square corners and the done/start circles, per
 * {@link GraphDiagramMigration}'s doc. */
const CORNER_RADIUS = 14;

/**
 * One migration sink's SVG shape — a labelled exit pill for a `graph:<name>` choice
 * target ({@link LaidOutMigration}), split out the same way `graph-diagram-node-shape.ts`
 * and `graph-diagram-start.ts` were (`web:lint` line cap): an
 * attribute-selector component (`g[appGraphDiagramMigration]`) rendering as a plain
 * `<g>` inside the parent's `<svg>`, one per distinct target graph name the parent
 * loops over.
 *
 * A rounded pill carrying the target graph's name (`bzh:migration-not-transition`).
 * Purely presentational: the parent supplies already-laid-out geometry.
 */
@Component({
  selector: 'g[appGraphDiagramMigration]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    'data-testid': 'graph-diagram-migration',
    '[attr.data-target-graph]': 'migration().targetGraph',
  },
  templateUrl: './graph-diagram-migration.html',
  styleUrl: './graph-diagram-migration.css',
})
export class GraphDiagramMigration {
  readonly migration = input.required<LaidOutMigration>();

  protected readonly cornerRadius = CORNER_RADIUS;
}
