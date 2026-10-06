import { ChangeDetectionStrategy, Component, effect, input, signal } from '@angular/core';

import type { GraphView } from 'fleet';
import { GraphDiagram } from './graph-diagram';
import { GraphDiagramDetail } from './graph-diagram-detail';
import type { DiagramSelection } from './graph-diagram-selection';

/**
 * The diagram's 50/50 split — `GraphDiagram` left, `GraphDiagramDetail` right —
 * and the sole owner of "what is selected", so the diagram and the pane can
 * never disagree about the current selection. This component holds no copy of the
 * laid-out graph (`GraphDiagram` owns layout).
 */
@Component({
  selector: 'app-graph-diagram-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GraphDiagram, GraphDiagramDetail],
  templateUrl: './graph-diagram-view.html',
  styleUrl: './graph-diagram-view.css',
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
