import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { LaidOutStart } from './graph-layout';

/**
 * The green START circle and its connector arrow into the entry node — split out of
 * `graph-diagram.ts` (issue #157's 400-line `web:structural-gate` cap) once the start
 * indicator (blizzard#207, replacing the old per-node yellow entry-ring box) pushed
 * the parent over it. Mirrors `graph-diagram-node-shape.ts`: an attribute-selector
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
  template: `
    <svg:path
      [attr.d]="start().path"
      class="edge-start"
      marker-end="url(#graph-diagram-arrow-advance)"
      data-testid="graph-diagram-start-path"
    />
    <svg:circle
      class="start-source"
      [attr.cx]="start().x"
      [attr.cy]="start().y"
      [attr.r]="start().r"
      data-testid="graph-diagram-start"
    />
    <svg:text class="start-label" [attr.x]="start().x" [attr.y]="start().y + 4" text-anchor="middle">START</svg:text>
  `,
  styles: `
    .edge-start {
      fill: none;
      stroke: var(--green);
      stroke-width: 2.25;
      pointer-events: none;
    }
    .start-source {
      fill: var(--green-dim);
      stroke: var(--green);
      stroke-width: 1.5;
    }
    .start-label {
      fill: var(--text);
      font-family: var(--mono);
      font-size: 10px;
      font-weight: 700;
      text-anchor: middle;
    }
  `,
})
export class GraphDiagramStart {
  readonly start = input.required<LaidOutStart>();
}
