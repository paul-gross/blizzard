/** One loading placeholder row, shaped like a real one: per work item, its count of title lines; and its links. */
export interface SkeletonRow {
  readonly items: readonly number[];
  readonly links: number;
}

/**
 * The list's loading placeholders, each drawn to a real row's shape — one or two work items of one to three title
 * lines, one to four links — from a fixed seed, so the placeholders hold still across renders.
 */
export function skeletonRows(count = 7, seed = 7): readonly SkeletonRow[] {
  let state = seed;
  const next = (low: number, high: number): number => {
    state = (state * 1103515245 + 12345) % 2147483648;
    return low + ((state >>> 16) % (high - low + 1));
  };
  return Array.from({ length: count }, () => ({
    items: Array.from({ length: next(1, 2) }, () => next(1, 3)),
    links: next(1, 4),
  }));
}
