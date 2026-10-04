import { hubApi, type GraphView } from 'fleet';

/**
 * What one choice's `to:` names — the wire edge's `target_kind` (a node in this
 * graph, the reserved `done` terminal, or a migration to another graph) resolved
 * into a laid-out target, and the structural kind that follows from which one it
 * is. `graph-layout.ts` re-exports {@link EdgeKind} and {@link EdgeTarget} so
 * consumers use one type entrypoint.
 */

const TargetKind = hubApi.ChoiceTargetKind;

/** An edge's derived semantic kind — purely structural, since the wire model
 * carries no `kind` field: an edge to the reserved `done` terminal, a migration to
 * another graph, or any forward-pointing edge, is `advance`; a self-loop or a back
 * edge (target declared no later than its source) is `retry`. A migration can never
 * be a self-loop or back edge — it names no node in this graph — so it is always
 * `advance`. */
export type EdgeKind = 'advance' | 'retry';

/** What a resolved edge (or the current selection) targets — one variant per
 * {@link hubApi.ChoiceTargetKind}: a node in this graph, the reserved `done`
 * terminal, or a migration to another graph entirely. */
export type EdgeTarget =
  | { readonly kind: typeof TargetKind.NODE; readonly nodeId: string }
  | { readonly kind: typeof TargetKind.DONE }
  | { readonly kind: typeof TargetKind.GRAPH; readonly targetGraph: string };

export interface ResolvedEdge {
  readonly id: string;
  readonly fromId: string;
  readonly target: EdgeTarget;
  readonly kind: EdgeKind;
  readonly label: string;
  readonly choiceId: string;
}

/** Resolves every edge's target — from the wire edge's `target_kind`, a node in
 * this graph, the reserved `done` terminal, or a migration to `target_graph` — and
 * its structural kind. Returns `null` if an edge names a target it cannot place (an
 * unknown node name, or a graph migration carrying no target graph) — a degenerate
 * graph the caller falls back on rather than mis-render.
 *
 * Only edges actually present in `graph.edges` are laid out here — the runtime's
 * machinery-default edges (e.g. a `deliver` node's implicit `landed→done` /
 * `conflict→entry`) are never part of the wire `GraphView` and are intentionally
 * *not* synthesized for the diagram, so the `done` sink (and any edge into it)
 * only renders when a real authored edge targets `done`. */
export function resolveEdges(graph: GraphView, nameToId: ReadonlyMap<string, string>): ResolvedEdge[] | null {
  const indexById = new Map(graph.nodes?.map((n, i) => [n.node_id, i]) ?? []);
  const nodeById = new Map(graph.nodes?.map((n) => [n.node_id, n]) ?? []);
  const resolved: ResolvedEdge[] = [];
  for (const [i, edge] of (graph.edges ?? []).entries()) {
    // The choice's name lives on the *source* node's `choices`, not the edge — the
    // edge only carries `choice_id` (mirrors `graph-detail.ts`'s `resolvedEdges`).
    const choice = nodeById.get(edge.from_node_id)?.choices?.find((c) => c.choice_id === edge.choice_id);
    const label = choice?.name ?? edge.choice_id;
    const targetKind = edge.target_kind ?? TargetKind.NODE;
    if (targetKind === TargetKind.DONE) {
      resolved.push({ id: `e${i}`, fromId: edge.from_node_id, target: { kind: TargetKind.DONE }, kind: 'advance', label, choiceId: edge.choice_id });
      continue;
    }
    if (targetKind === TargetKind.GRAPH) {
      const targetGraph = edge.target_graph;
      if (!targetGraph) return null;
      resolved.push({
        id: `e${i}`,
        fromId: edge.from_node_id,
        target: { kind: TargetKind.GRAPH, targetGraph },
        kind: 'advance',
        label,
        choiceId: edge.choice_id,
      });
      continue;
    }
    const toId = nameToId.get(edge.to_node_name);
    if (toId === undefined) return null;
    const fromIndex = indexById.get(edge.from_node_id);
    const toIndex = indexById.get(toId);
    if (fromIndex === undefined || toIndex === undefined) return null;
    const isSelfLoop = toId === edge.from_node_id;
    const isBackEdge = !isSelfLoop && toIndex <= fromIndex;
    const kind: EdgeKind = isSelfLoop || isBackEdge ? 'retry' : 'advance';
    resolved.push({ id: `e${i}`, fromId: edge.from_node_id, target: { kind: TargetKind.NODE, nodeId: toId }, kind, label, choiceId: edge.choice_id });
  }
  return resolved;
}
