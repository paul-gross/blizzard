import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { META_FIRST_LINE_Y, META_LINE_HEIGHT, type LaidOutNode } from './graph-layout';

/** The node box's corner radius — square on the left (the color stripe's edge),
 * rounded on the right, so the selection/hover outline traced over the same path
 * hugs the shape actually drawn instead of a uniformly rounded rect (blizzard#207). */
const CORNER_RADIUS = 9;

/**
 * One node's SVG shape — split out of `graph-diagram.ts` (issue #157's 400-line
 * `web:structural-gate` cap) once the selection feature (blizzard#159) pushed the
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
  template: `
    <svg:path class="node-box" [attr.d]="boxPath()" />
    <svg:rect class="node-stripe" [attr.x]="node().x" [attr.y]="node().y" width="4" [attr.height]="node().height" />
    <svg:text class="node-name" [attr.x]="node().x + 14" [attr.y]="node().y + 24" data-testid="graph-diagram-node-name">
      {{ node().name }}
    </svg:text>
    <svg:text
      class="node-badge"
      [attr.x]="node().x + node().width - 8"
      [attr.y]="node().y + 20"
      text-anchor="end"
      data-testid="graph-diagram-node-badge"
    >
      {{ node().executor.toUpperCase() }}
    </svg:text>
    @for (line of node().metaLines; track $index) {
      <svg:text class="node-meta" [attr.x]="node().x + 14" [attr.y]="metaLineY($index)">{{ line }}</svg:text>
    }
  `,
  styles: `
    :host {
      cursor: pointer;
    }
    .node-box {
      fill: var(--panel);
      stroke: var(--bezel-hi);
      stroke-width: 1.25;
    }
    .node-stripe {
      fill: var(--label-dim);
    }
    :host(.exec-runner) .node-stripe {
      fill: var(--cyan);
    }
    :host(.exec-hub) .node-stripe {
      fill: var(--amber);
    }
    :host(:hover) .node-box {
      fill: var(--panel-hover);
    }
    :host(.selected) .node-box {
      fill: var(--panel-hi);
      stroke: var(--amber-hi);
      stroke-width: 2.5;
    }
    :host(.incident) .node-box {
      stroke: var(--cyan);
      stroke-width: 2;
    }
    .node-name {
      fill: var(--text);
      font-family: var(--mono);
      font-size: 13px;
      font-weight: 600;
    }
    .node-badge {
      fill: var(--label-dim);
      font-family: var(--mono);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.06em;
    }
    :host(.exec-runner) .node-badge {
      fill: var(--cyan);
    }
    :host(.exec-hub) .node-badge {
      fill: var(--amber);
    }
    .node-meta {
      fill: var(--label);
      font-family: var(--mono);
      font-size: 11px;
    }
  `,
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
