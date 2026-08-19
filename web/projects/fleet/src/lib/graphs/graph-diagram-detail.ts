import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { GraphNodeView, GraphView } from '../api/hub';
import { KitPanel } from '../kit/kit-panel';
import { type DiagramSelection, resolveSelectedChoice, resolveSelectedNode } from './graph-diagram-selection';
import { listOrDash, producesNames, retriesLabel, sessionLabel } from './graph-node';

/**
 * The diagram's detail pane — the selected node's full `GraphNodeView` (including
 * `prompt`/`judgement_prompt`), the selected edge's choice (including its
 * `prompt_addendum`, issue #208), or a neutral hint when nothing is selected.
 * Presentational only: `graph`/`selection` are plain inputs,
 * resolved against `graph-diagram-selection.ts`'s pure resolvers
 * (`bzh:frontend-container-presentational`). Field rendering (retries, produces,
 * session) goes through `graph-node.ts`'s shared helpers — the same ones
 * `graph-node-table.ts` uses — so the pane and the structured table can't drift on
 * how a field displays. Chrome comes from `fleet/lib/kit/` (`bzh:frontend-kit-floor`).
 */
@Component({
  selector: 'fleet-graph-diagram-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel],
  templateUrl: './graph-diagram-detail.html',
  styleUrl: './graph-diagram-detail.css',
})
export class GraphDiagramDetail {
  readonly graph = input.required<GraphView>();
  readonly selection = input<DiagramSelection | null>(null);

  protected readonly selectedNode = computed<GraphNodeView | null>(() => resolveSelectedNode(this.graph(), this.selection()));
  protected readonly selectedChoice = computed(() => resolveSelectedChoice(this.graph(), this.selection()));

  protected nodeName(nodeId: string): string {
    return (this.graph().nodes ?? []).find((n) => n.node_id === nodeId)?.name ?? nodeId;
  }

  protected readonly retriesLabel = retriesLabel;
  protected readonly listOrDash = listOrDash;
  protected readonly producesNames = producesNames;
  protected readonly sessionLabel = sessionLabel;
}
