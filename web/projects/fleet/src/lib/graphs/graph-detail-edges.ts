import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { GraphEdgeView, GraphNodeView } from '../api/hub';

/** One outgoing edge, resolved against the choice it fires on (the choice lives on
 * the *source* node's `choices`, edges only carry the `choice_id`). */
interface ResolvedEdge {
  readonly edge: GraphEdgeView;
  readonly choiceName: string;
  readonly choiceDescription: string;
}

/**
 * The graph detail's **edges & choices** section — every node with outgoing edges,
 * each resolved against the choice it fires on and its prompt addendum.
 *
 * Presentational only: `nodes`/`edges` are plain inputs, no query injection
 * (`bzh:frontend-container-presentational`) — the shape `graph-node-table.ts` and
 * `graph-session-table.ts` already take, rather than the whole `GraphView`.
 */
@Component({
  selector: 'fleet-graph-detail-edges',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './graph-detail-edges.html',
  styleUrl: './graph-detail-edges.css',
})
export class GraphDetailEdges {
  readonly nodes = input.required<readonly GraphNodeView[]>();
  readonly edges = input.required<readonly GraphEdgeView[]>();

  /** This node's outgoing edges, each resolved against the matching choice on the
   * same node (edges only carry `choice_id`; the choice's name/description live on
   * the source node's `choices`). */
  protected resolvedEdges(node: GraphNodeView): readonly ResolvedEdge[] {
    const choices = node.choices ?? [];
    return this.edges()
      .filter((edge) => edge.from_node_id === node.node_id)
      .map((edge) => {
        const choice = choices.find((c) => c.choice_id === edge.choice_id);
        return {
          edge,
          choiceName: choice?.name ?? edge.choice_id,
          choiceDescription: choice?.description ?? '',
        };
      });
  }
}
