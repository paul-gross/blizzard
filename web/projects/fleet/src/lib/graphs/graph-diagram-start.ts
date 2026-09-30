import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { LaidOutStart } from './graph-layout';

/**
 * The green START circle and its connector arrow into the entry node.
 * Mirrors `graph-diagram-node-shape.ts`: an attribute-selector
 * component (`g[fleetGraphDiagramStart]`) so it renders as a plain `<g>` inside the
 * parent's `<svg>`. Purely presentational — `start` is the already-laid-out geometry
 * `graph-layout.ts` computed; the parent's `<defs>` still owns the shared
 * `graph-diagram-arrow-advance` marker this reuses, since a marker is defined once
 * per document, not per instance.
 */
@Component({
  selector: 'g[fleetGraphDiagramStart]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    'data-testid': 'graph-diagram-start-group',
  },
  templateUrl: './graph-diagram-start.html',
  styleUrl: './graph-diagram-start.css',
})
export class GraphDiagramStart {
  readonly start = input.required<LaidOutStart>();
}
