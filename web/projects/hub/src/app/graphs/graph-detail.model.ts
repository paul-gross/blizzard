import type { GraphNodeView, GraphView } from 'fleet';
import type { GraphLifecycleVars } from './graph-lifecycle.mutations';

/** The graph's `retired` flag as it will read once a pending retire/enable for
 * `graphId` settles (`bzh:frontend-pending-override`) — `null` while none is pending. */
export function graphOverrideRetired(graphId: string, pending: readonly GraphLifecycleVars[]): boolean | null {
  return pending.find((vars) => vars.graphId === graphId)?.retired ?? null;
}

/** The name of the graph's entry node — its raw `entry_node_id` when no node carries
 * that id, and `''` before the graph resolves. */
export function entryNodeName(graph: GraphView | undefined, nodes: readonly GraphNodeView[]): string {
  if (!graph) return '';
  return nodes.find((n) => n.node_id === graph.entry_node_id)?.name ?? graph.entry_node_id;
}
