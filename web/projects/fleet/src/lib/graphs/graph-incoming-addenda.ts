import type { GraphNodeView, GraphView } from '../api/hub';

/** One inbound `prompt_addendum` arriving at a selected node — the edge's own
 * addendum text plus where it fires from, so the detail pane can label several
 * at once (a node commonly has more than one inbound route). */
export interface IncomingAddendum {
  /** The source node's name, or its raw `from_node_id` when it names no node in
   * `graph` — `graph-diagram-detail.ts`'s own `nodeName` fallback, mirrored here
   * so the two can't disagree on a degenerate id. */
  readonly fromNodeName: string;
  /** The firing choice's name, or its raw `choice_id` when it matches none on the
   * source node's `choices` — `graph-diagram-selection.ts`'s `resolveSelectedChoice`
   * fallback, mirrored here for the same reason. */
  readonly choiceName: string;
  readonly promptAddendum: string;
}

/**
 * Every non-empty `prompt_addendum` on an edge that targets `node` — matched by
 * `to_node_name === node.name`, since `GraphEdgeView` carries only the target's
 * *name*, never its id. `to_node_name` is also widened to a `graph:<name>` form
 * for a cross-graph migration target; such a value never equals a node's own
 * `name`, so this match is unaffected by it. Ordered as `graph.edges` lists them.
 */
export function incomingAddenda(graph: GraphView, node: GraphNodeView): readonly IncomingAddendum[] {
  const nodes = graph.nodes ?? [];
  const result: IncomingAddendum[] = [];
  for (const edge of graph.edges ?? []) {
    if (edge.to_node_name !== node.name || !edge.prompt_addendum) continue;
    const fromNode = nodes.find((n) => n.node_id === edge.from_node_id);
    const choice = fromNode?.choices?.find((c) => c.choice_id === edge.choice_id);
    result.push({
      fromNodeName: fromNode?.name ?? edge.from_node_id,
      choiceName: choice?.name ?? edge.choice_id,
      promptAddendum: edge.prompt_addendum,
    });
  }
  return result;
}
