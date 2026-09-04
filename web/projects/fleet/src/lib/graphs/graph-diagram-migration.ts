import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { LaidOutMigration } from './graph-layout';

/** The migration pill's corner radius — rounded enough to read as a pill next to the
 * node box's mostly-square corners and the done/start circles, per
 * {@link GraphDiagramMigration}'s doc. */
const CORNER_RADIUS = 14;

/**
 * One migration sink's SVG shape — a labelled exit pill for a `graph:<name>` choice
 * target ({@link LaidOutMigration}), split out the same way `graph-diagram-node-shape.ts`
 * and `graph-diagram-start.ts` were (issue #157's `web:lint` line cap): an
 * attribute-selector component (`g[fleetGraphDiagramMigration]`) rendering as a plain
 * `<g>` inside the parent's `<svg>`, one per distinct target graph name the parent
 * loops over.
 *
 * Deliberately not a node box (no executor stripe/badge — this node is not part of
 * *this* graph) and not the `done` circle (a migration does not end the chunk, it
 * re-pins it elsewhere, `bzh:migration-not-transition`): a rounded pill carrying the
 * target graph's name is the third, visually distinct shape the diagram's other two
 * exits (the node box and the `done` circle) leave room for. Purely presentational,
 * same contract as its siblings: the parent supplies already-laid-out geometry, no
 * selection logic lives here.
 */
@Component({
  selector: 'g[fleetGraphDiagramMigration]',
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
