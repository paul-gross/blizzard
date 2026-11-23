import type { Graph } from './graph.domain';

/**
 * What one choice's `to:` names — the edge's `targetKind` (a node in this
 * graph, the reserved `done` terminal, or a migration to another graph) resolved
 * into a laid-out target, and the structural kind that follows from which one it
 * is.
 */

/** An edge's derived semantic kind — purely structural, since the edge model
 * carries no `kind` field: an edge to the reserved `done` terminal, a migration to
 * another graph, or any forward-pointing edge, is `advance`; a self-loop or a back
 * edge (target declared no later than its source) is `retry`. A migration can never
 * be a self-loop or back edge — it names no node in this graph — so it is always
 * `advance`. */
export type EdgeKind = 'advance' | 'retry';

/** What a resolved edge (or the current selection) targets — one variant per
 * known choice target kind: a node in this graph, the reserved `done`
 * terminal, or a migration to another graph entirely. */
export type EdgeTarget =
  | { readonly kind: 'node'; readonly nodeId: string }
  | { readonly kind: 'done' }
  | { readonly kind: 'graph'; readonly targetGraph: string };

export interface ResolvedEdge {
  readonly id: string;
  readonly fromId: string;
  readonly target: EdgeTarget;
  readonly kind: EdgeKind;
  readonly label: string;
  readonly choiceId: string;
}

/** Resolves every edge's target — from the edge's `targetKind`, a node in
 * this graph, the reserved `done` terminal, or a migration to `targetGraph` — and
 * its structural kind. Returns `null` if an edge names a target it cannot place (an
 * unknown node name, or a graph migration carrying no target graph). An edge whose
 * `targetKind` this build does not list is skipped, so the rest of the diagram
 * still renders.
 *
 * Only edges present in `graph.edges` are resolved. */
export function resolveEdges(graph: Graph, nameToId: ReadonlyMap<string, string>): ResolvedEdge[] | null {
  const indexById = new Map(graph.nodes?.map((n, i) => [n.nodeId, i]) ?? []);
  const nodeById = new Map(graph.nodes?.map((n) => [n.nodeId, n]) ?? []);
  const resolved: ResolvedEdge[] = [];
  for (const [i, edge] of (graph.edges ?? []).entries()) {
    // The choice's name lives on the *source* node's `choices`, not the edge — the
    // edge only carries `choiceId` (mirrors `graph-detail.ts`'s `resolvedEdges`).
    const choice = nodeById.get(edge.fromNodeId)?.choices?.find((c) => c.choiceId === edge.choiceId);
    const label = choice?.name ?? edge.choiceId;
    const targetKind = edge.targetKind;
    if (targetKind === 'done') {
      resolved.push({ id: `e${i}`, fromId: edge.fromNodeId, target: { kind: 'done' }, kind: 'advance', label, choiceId: edge.choiceId });
      continue;
    }
    if (targetKind === 'graph') {
      const targetGraph = edge.targetGraph;
      if (!targetGraph) return null;
      resolved.push({
        id: `e${i}`,
        fromId: edge.fromNodeId,
        target: { kind: 'graph', targetGraph },
        kind: 'advance',
        label,
        choiceId: edge.choiceId,
      });
      continue;
    }
    // A target kind a newer hub added: this build cannot place it, so it draws no edge.
    if (targetKind !== 'node') continue;
    const toId = nameToId.get(edge.toNodeName);
    if (toId === undefined) return null;
    const fromIndex = indexById.get(edge.fromNodeId);
    const toIndex = indexById.get(toId);
    if (fromIndex === undefined || toIndex === undefined) return null;
    const isSelfLoop = toId === edge.fromNodeId;
    const isBackEdge = !isSelfLoop && toIndex <= fromIndex;
    const kind: EdgeKind = isSelfLoop || isBackEdge ? 'retry' : 'advance';
    resolved.push({ id: `e${i}`, fromId: edge.fromNodeId, target: { kind: 'node', nodeId: toId }, kind, label, choiceId: edge.choiceId });
  }
  return resolved;
}
