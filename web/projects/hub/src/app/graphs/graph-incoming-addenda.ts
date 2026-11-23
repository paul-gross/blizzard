import type { Graph, GraphNode } from './graph.domain';

/** One inbound `promptAddendum` arriving at a selected node — the edge's own
 * addendum text plus where it fires from, so the detail pane can label several
 * at once (a node commonly has more than one inbound route). */
export interface IncomingAddendum {
  /** The source node's name, or its raw `fromNodeId` when it names no node in
   * `graph` — `graph-diagram-detail.ts`'s own `nodeName` fallback, mirrored here
   * so the two can't disagree on a degenerate id. */
  readonly fromNodeName: string;
  /** The firing choice's name, or its raw `choiceId` when it matches none on the
   * source node's `choices` — `graph-diagram-selection.ts`'s `resolveSelectedChoice`
   * fallback, mirrored here for the same reason. */
  readonly choiceName: string;
  readonly promptAddendum: string;
}

/**
 * Every non-empty `promptAddendum` on an edge that targets `node` — matched by
 * `toNodeName === node.name`, since `GraphEdge` carries only the target's
 * *name*, never its id. `toNodeName` is also widened to a `graph:<name>` form
 * for a cross-graph migration target; such a value never equals a node's own
 * `name`, so this match is unaffected by it. Ordered as `graph.edges` lists them.
 */
export function incomingAddenda(graph: Graph, node: GraphNode): readonly IncomingAddendum[] {
  const nodes = graph.nodes ?? [];
  const result: IncomingAddendum[] = [];
  for (const edge of graph.edges ?? []) {
    if (edge.toNodeName !== node.name || !edge.promptAddendum) continue;
    const fromNode = nodes.find((n) => n.nodeId === edge.fromNodeId);
    const choice = fromNode?.choices?.find((c) => c.choiceId === edge.choiceId);
    result.push({
      fromNodeName: fromNode?.name ?? edge.fromNodeId,
      choiceName: choice?.name ?? edge.choiceId,
      promptAddendum: edge.promptAddendum,
    });
  }
  return result;
}
