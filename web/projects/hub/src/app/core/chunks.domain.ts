/** A node's latest visit in a chunk's graph. Unknown states retain the server's value. */
export type GraphProgressState =
  | { readonly kind: 'current' | 'passed' | 'failed' | 'unvisited' }
  | { readonly kind: 'unknown'; readonly raw: string };

export interface GraphProgressNode {
  readonly nodeId: string;
  readonly name: string;
  readonly state: GraphProgressState;
}

export interface BoardDoneWindow {
  readonly done_since: string;
  readonly floor: number;
  readonly window_seconds: number;
}

/** Metadata for each drained page, kept separately from the raw cached pages. */
export interface BoardPageMetadata {
  readonly next_cursor: string | null;
  readonly board_done_policy: BoardDoneWindow | null | undefined;
}
