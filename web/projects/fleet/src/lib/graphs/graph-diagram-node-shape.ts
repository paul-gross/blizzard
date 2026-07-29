import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { META_FIRST_LINE_Y, META_LINE_HEIGHT, type LaidOutNode } from './graph-layout';

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
    '[class.entry]': 'node().isEntry',
    '[class.exec-hub]': "node().executor === 'hub'",
    '[class.exec-runner]': "node().executor !== 'hub'",
    '[class.selected]': 'selected()',
    '[class.incident]': 'incident()',
  },
  template: `
    @if (node().isEntry) {
      <svg:rect
        class="entry-ring"
        [attr.x]="node().x - 4"
        [attr.y]="node().y - 4"
        [attr.width]="node().width + 8"
        [attr.height]="node().height + 8"
        rx="12"
        data-testid="graph-diagram-entry-ring"
      />
    }
    <svg:rect class="node-box" [attr.x]="node().x" [attr.y]="node().y" [attr.width]="node().width" [attr.height]="node().height" rx="9" />
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
    .entry-ring {
      fill: none;
      stroke: var(--amber-hi);
      stroke-width: 2;
    }
    :host(.selected) .node-box {
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
}
