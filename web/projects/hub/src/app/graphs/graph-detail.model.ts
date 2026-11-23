import type { GraphLifecycleVars } from './graph-lifecycle.mutations';
import type { Graph, GraphNode } from './graph.domain';

/** The graph's `retired` flag as it will read once a pending retire/enable for
 * `graphId` settles (`bzh:frontend-pending-override`) — `null` while none is pending. */
export function graphOverrideRetired(graphId: string, pending: readonly GraphLifecycleVars[]): boolean | null {
  return pending.find((vars) => vars.graphId === graphId)?.retired ?? null;
}

/** The name of the graph's entry node — its raw `entryNodeId` when no node carries
 * that id, and `''` before the graph resolves. */
export function entryNodeName(graph: Graph | undefined, nodes: readonly GraphNode[]): string {
  if (!graph) return '';
  return nodes.find((n) => n.nodeId === graph.entryNodeId)?.name ?? graph.entryNodeId;
}
