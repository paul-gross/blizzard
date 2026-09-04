import type { GraphView } from '../api/hub';

/**
 * What one choice's `to:` names — resolved from the wire's plain-string
 * `to_node_name` into the three things it can mean, and the structural kind that
 * follows from which one it is. Split out of `graph-layout.ts` (issue #157's
 * `web:lint` 400-line cap, the same reason `graph-diagram-node-shape.ts` and
 * `graph-diagram-start.ts` were split out of the diagram component) into its own
 * cohesive unit — this mirrors the backend's own split, where `ChoiceTarget` is a
 * dataclass separate from the graph module it lives beside
 * (`src/blizzard/hub/domain/graph.py`). `graph-layout.ts` re-exports
 * {@link EdgeKind} and {@link EdgeTarget} so no consumer outside this pair has to
 * know the type moved.
 */

/** A node's declaration in `graph.nodes` names the terminal a choice can point at
 * instead of a node — the domain's `RESERVED_TERMINAL`
 * (`src/blizzard/hub/domain/graph.py`). Duplicated here (not a backend import) since
 * the wire model carries it as a plain string, not a discriminated value. */
export const DONE_TERMINAL = 'done';

/** The cross-graph migration prefix a choice's `to:` may carry instead of a node
 * name or the reserved terminal — the domain's `GRAPH_TARGET_PREFIX`
 * (`src/blizzard/hub/domain/graph.py`). Duplicated here for the same reason as
 * {@link DONE_TERMINAL}: the wire model carries `to_node_name` as a plain string,
 * not a discriminated value. */
export const GRAPH_TARGET_PREFIX = 'graph:';

/** An edge's derived semantic kind — purely structural, since the wire model
 * carries no `kind` field: an edge to the reserved `done` terminal, a migration to
 * another graph, or any forward-pointing edge, is `advance`; a self-loop or a back
 * edge (target declared no later than its source) is `retry`. A migration can never
 * be a self-loop or back edge — it names no node in this graph — so it is always
 * `advance`. */
export type EdgeKind = 'advance' | 'retry';

/** What a resolved edge (or the current selection) targets — the three things a
 * choice's `to:` can name (`ChoiceTarget` in `src/blizzard/hub/domain/graph.py`):
 * a node in this graph, the reserved `done` terminal, or a migration to another
 * graph entirely. Replaces the old `toNodeId: string | null` encoding, which had
 * no room for the third case. */
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

/** Resolves every edge's target — a node in this graph, the reserved `done`
 * terminal, or a `graph:<name>` migration — and its structural kind. Returns `null`
 * if an edge names a target that matches none of the three (an unknown node name, or
 * a malformed `graph:` value — empty or carrying a further `:`, mirroring the
 * domain's `ChoiceTarget.malformed`) — a degenerate graph the caller falls back on
 * rather than mis-render.
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
    if (edge.to_node_name === DONE_TERMINAL) {
      resolved.push({ id: `e${i}`, fromId: edge.from_node_id, target: { kind: 'done' }, kind: 'advance', label, choiceId: edge.choice_id });
      continue;
    }
    if (edge.to_node_name.startsWith(GRAPH_TARGET_PREFIX)) {
      const targetGraph = edge.to_node_name.slice(GRAPH_TARGET_PREFIX.length);
      if (targetGraph === '' || targetGraph.includes(':')) return null;
      resolved.push({
        id: `e${i}`,
        fromId: edge.from_node_id,
        target: { kind: 'graph', targetGraph },
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
    resolved.push({ id: `e${i}`, fromId: edge.from_node_id, target: { kind: 'node', nodeId: toId }, kind, label, choiceId: edge.choice_id });
  }
  return resolved;
}
