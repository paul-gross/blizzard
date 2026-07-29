import type { GraphChoiceView, GraphNodeView, GraphView } from '../api/hub';
import type { EdgeKind, LaidOutGraph } from './graph-layout';

/**
 * What is selected in the diagram — structural, not DOM: a node by id, or an edge
 * carrying what only the layout knows about it (endpoints, `choiceId`, `edgeKind`),
 * so the detail pane can render "source → target" without re-deriving it from
 * `GraphView`. Pure and DOM-free (`graph-diagram.ts` is the only emitter,
 * `graph-diagram-detail.ts` the only consumer) so this module unit-tests without a
 * browser, matching `graph-layout.ts`'s seam.
 */
export type DiagramSelection =
  | { readonly kind: 'node'; readonly nodeId: string }
  | {
      readonly kind: 'edge';
      readonly edgeId: string;
      readonly fromNodeId: string;
      /** `null` when the edge targets the reserved `done` terminal. */
      readonly toNodeId: string | null;
      readonly choiceId: string;
      readonly edgeKind: EdgeKind;
    };

/** A comparable id for template class/attribute binding — two selections of the
 * same node or edge produce the same key, `null` for no selection. */
export function selectionKey(selection: DiagramSelection | null): string | null {
  if (selection === null) return null;
  return selection.kind === 'node' ? `node:${selection.nodeId}` : `edge:${selection.edgeId}`;
}

/** The ids of every edge and self-loop incident to `nodeId` — its outgoing edges,
 * incoming edges, and its own self-loop (if any). Drives the "highlight the node's
 * in/out edges" requirement. */
export function incidentEdgeIds(laidOut: LaidOutGraph, nodeId: string): readonly string[] {
  const ids: string[] = [];
  for (const edge of laidOut.edges) {
    if (edge.fromNodeId === nodeId || edge.toNodeId === nodeId) ids.push(edge.id);
  }
  for (const loop of laidOut.selfLoops) {
    if (loop.nodeId === nodeId) ids.push(loop.id);
  }
  return ids;
}

/** The node ids an edge selection's endpoints highlight — one id for a self-loop or
 * an edge into `done` (`toNodeId === null` contributes nothing), two otherwise. */
export function endpointNodeIds(selection: DiagramSelection | null): readonly string[] {
  if (selection === null || selection.kind !== 'edge') return [];
  const ids = [selection.fromNodeId];
  if (selection.toNodeId !== null && selection.toNodeId !== selection.fromNodeId) ids.push(selection.toNodeId);
  return ids;
}

/** The selected node's full wire view, or `null` when nothing (or an edge) is
 * selected, or the selected id names no node in `graph` (a stale selection after
 * the `graph` input changes out from under it). */
export function resolveSelectedNode(graph: GraphView, selection: DiagramSelection | null): GraphNodeView | null {
  if (selection === null || selection.kind !== 'node') return null;
  return (graph.nodes ?? []).find((n) => n.node_id === selection.nodeId) ?? null;
}

/** A selected edge's choice, resolved against its source node — the pane's view of
 * "source → target" plus the choice's name/description/kind. */
export interface ResolvedChoiceSelection {
  readonly choiceId: string;
  /** The choice's name, or the raw `choiceId` when it matches no choice on the
   * source node — mirrors `graph-detail.ts`'s `resolvedEdges` fallback so the
   * diagram and the structured table never disagree on a degenerate choice id. */
  readonly name: string;
  readonly description: string;
  readonly fromNodeId: string;
  readonly toNodeId: string | null;
  readonly edgeKind: EdgeKind;
}

/** The selected edge's choice, or `null` when nothing (or a node) is selected. */
export function resolveSelectedChoice(graph: GraphView, selection: DiagramSelection | null): ResolvedChoiceSelection | null {
  if (selection === null || selection.kind !== 'edge') return null;
  const fromNode = (graph.nodes ?? []).find((n) => n.node_id === selection.fromNodeId);
  const choice = fromNode?.choices?.find((c: GraphChoiceView) => c.choice_id === selection.choiceId);
  return {
    choiceId: selection.choiceId,
    name: choice?.name ?? selection.choiceId,
    description: choice?.description ?? '',
    fromNodeId: selection.fromNodeId,
    toNodeId: selection.toNodeId,
    edgeKind: selection.edgeKind,
  };
}
