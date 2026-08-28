import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { META_FIRST_LINE_Y, META_LINE_HEIGHT } from './graph-box-sizing';
import type { LaidOutNode } from './graph-layout';

/** The node box's corner radius — square on the left (the color stripe's edge),
 * rounded on the right, so the selection/hover outline traced over the same path
 * hugs the shape actually drawn instead of a uniformly rounded rect (blizzard#207). */
const CORNER_RADIUS = 9;

/**
 * One node's SVG shape — split out of `graph-diagram.ts` (issue #157's 400-line
 * `web:lint` cap) once the selection feature (blizzard#159) pushed the
 * parent over it. An attribute-selector component (`g[fleetGraphDiagramNode]`)
 * so it renders as a plain `<g>` inside the parent's `<svg>`, no wrapping element.
 * Purely presentational: `selected`/`incident` are booleans the parent derives from
 * `graph-diagram-selection.ts`; the click listener stays on the parent's usage site
 * (`(click)` on a native host element needs no component involvement), so this
 * component owns drawing only, never selection logic.
 */
@Component({
  selector: 'g[fleetGraphDiagramNode]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    'data-testid': 'graph-diagram-node',
    '[attr.data-node-id]': 'node().id',
    '[attr.data-selected]': "selected() ? 'true' : null",
    '[attr.data-incident]': "incident() ? 'true' : null",
    '[class.exec-hub]': "node().executor === 'hub'",
    '[class.exec-runner]': "node().executor !== 'hub'",
    '[class.selected]': 'selected()',
    '[class.incident]': 'incident()',
  },
  templateUrl: './graph-diagram-node-shape.html',
  styleUrl: './graph-diagram-node-shape.css',
})
export class GraphDiagramNodeShape {
  readonly node = input.required<LaidOutNode>();
  readonly selected = input(false);
  readonly incident = input(false);

  /** Baseline y of the node's `index`-th wrapped meta line — the same step
   * `graph-layout.ts` grew the box height by, so the lines land inside it. */
  protected metaLineY(index: number): number {
    return this.node().y + META_FIRST_LINE_Y + index * META_LINE_HEIGHT;
  }

  /** The node box's outline: square left corners, rounded right corners. A plain
   * `rect`'s `rx` rounds all four uniformly, so the shape (and anything stroked
   * over it, e.g. the selection outline) is drawn as an explicit path instead. */
  protected boxPath(): string {
    const n = this.node();
    const { x, y, width, height } = n;
    const r = CORNER_RADIUS;
    return (
      `M ${x} ${y} ` +
      `L ${x + width - r} ${y} ` +
      `A ${r} ${r} 0 0 1 ${x + width} ${y + r} ` +
      `L ${x + width} ${y + height - r} ` +
      `A ${r} ${r} 0 0 1 ${x + width - r} ${y + height} ` +
      `L ${x} ${y + height} Z`
    );
  }
}
