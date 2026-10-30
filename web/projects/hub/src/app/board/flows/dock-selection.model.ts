/** What the board's dock shows: a chunk, a flow's record, or nothing. */
export type DockSelection =
  | { readonly view: 'chunk'; readonly chunkId: string }
  | { readonly view: 'flow'; readonly flowId: string }
  | { readonly view: 'none' };

/** The dock's view from the URL's `?chunk=` and `?flow=`. The two are exclusive — every pick writes one and clears the
 * other — so a URL carrying both was written by hand, and the chunk wins. */
export function dockSelection(chunkId: string | null, flowId: string | null): DockSelection {
  if (chunkId !== null) return { view: 'chunk', chunkId };
  if (flowId !== null) return { view: 'flow', flowId };
  return { view: 'none' };
}

/** The query params a pick writes: the picked one set, the other cleared. */
export function dockParams(pick: { readonly chunk: string | null } | { readonly flow: string | null }): {
  chunk: string | null;
  flow: string | null;
} {
  return 'chunk' in pick ? { chunk: pick.chunk, flow: null } : { chunk: null, flow: pick.flow };
}
