import { ChangeDetectionStrategy, Component, effect, input, signal } from '@angular/core';

import type { GraphView } from '../api/hub';
import { GraphDiagram } from './graph-diagram';
import { GraphDiagramDetail } from './graph-diagram-detail';
import type { DiagramSelection } from './graph-diagram-selection';

/**
 * The diagram's 50/50 split — `GraphDiagram` left, `GraphDiagramDetail` right —
 * and the sole owner of "what is selected" (blizzard#159). `GraphDiagram` stays
 * fully controlled (it renders `selection`, emits `selectionChange`); this
 * component is the one place those two meet, so the diagram and the pane can
 * never disagree about the current selection. Layout runs once, inside
 * `GraphDiagram`'s own `GRAPH_LAYOUT` seam — this component holds no second copy
 * of the laid-out graph.
 */
@Component({
  selector: 'fleet-graph-diagram-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GraphDiagram, GraphDiagramDetail],
  template: `
    <div class="split" data-testid="graph-diagram-view">
      <fleet-graph-diagram
        class="pane-diagram"
        [graph]="graph()"
        [selection]="selection()"
        (selectionChange)="onSelectionChange($event)"
      />
      <fleet-graph-diagram-detail class="pane-detail" [graph]="graph()" [selection]="selection()" />
    </div>
  `,
  styles: `
    .split {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      min-height: 0;
    }
    .pane-diagram,
    .pane-detail {
      /* Without this the grid track never shrinks below the diagram's intrinsic
         content width, and its own \`.diagram-scroll\` overflow-x never engages —
         the well-known CSS-grid "min-width: auto" trap. */
      min-width: 0;
    }
    @media (max-width: 720px) {
      .split {
        grid-template-columns: 1fr;
      }
    }
  `,
})
export class GraphDiagramView {
  /** The already-fetched graph — passed straight through to both children
   * (`bzh:generated-client`; no re-fetch here). */
  readonly graph = input.required<GraphView>();

  protected readonly selection = signal<DiagramSelection | null>(null);

  /** Clears the selection whenever a different graph is shown — mirrors
   * `chunk-detail.ts`'s reset-on-id-change `effect()`; a selection carrying node
   * or edge ids from the *previous* graph would resolve to nothing (or, worse,
   * to an unrelated id that happens to collide) in the new one. */
  constructor() {
    effect(() => {
      void this.graph().graph_id;
      this.selection.set(null);
    });
  }

  protected onSelectionChange(selection: DiagramSelection | null): void {
    this.selection.set(selection);
  }
}
