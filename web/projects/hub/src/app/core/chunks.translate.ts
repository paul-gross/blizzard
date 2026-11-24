import type { hubApi } from 'fleet';
import type { GraphProgressNode, GraphProgressState } from './chunks.domain';

/** Read the open graph-progress vocabulary without losing an unrecognized value. */
export function fromGraphProgressState(state: hubApi.GraphProgressState): GraphProgressState {
  switch (state) {
    case 'current':
    case 'passed':
    case 'failed':
    case 'unvisited':
      return { kind: state };
    default:
      return { kind: 'unknown', raw: state };
  }
}

export function fromGraphProgressNode(node: hubApi.GraphProgressNodeView): GraphProgressNode {
  return {
    nodeId: node.node_id,
    name: node.name,
    state: fromGraphProgressState(node.state),
  };
}
