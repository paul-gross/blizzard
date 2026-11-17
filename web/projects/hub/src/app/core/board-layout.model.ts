/** Every way the board lays out its chunks: the lane grid, one list of full-width rows, or each environment's node
 * steps over time. */
export const BOARD_LAYOUTS = ['board', 'list', 'tracks'] as const;

/** How the board lays out its chunks — one of {@link BOARD_LAYOUTS}. */
export type BoardLayout = (typeof BOARD_LAYOUTS)[number];

/** Whether a value names one of the board's layouts, exactly. */
export function isBoardLayout(value: string | null): value is BoardLayout {
  return (BOARD_LAYOUTS as readonly (string | null)[]).includes(value);
}

/** A layout named by a tab or the URL, falling back to the lane grid for anything else. */
export function boardLayoutOf(value: string | null): BoardLayout {
  return isBoardLayout(value) ? value : 'board';
}
