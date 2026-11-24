/** A node's latest visit in a chunk's graph. Unknown states retain the server's value. */
export type GraphProgressState =
  | { readonly kind: 'current' | 'passed' | 'failed' | 'unvisited' }
  | { readonly kind: 'unknown'; readonly raw: string };

export interface GraphProgressNode {
  readonly nodeId: string;
  readonly name: string;
  readonly state: GraphProgressState;
}
