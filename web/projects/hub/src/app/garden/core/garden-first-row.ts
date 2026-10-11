/**
 * The row a gardening list opens on when its URL names none: the first row, on desktop, once the list's read has
 * settled. `null` when nothing should move — a row is already named, the read is in flight or failed, the list is
 * empty, or the viewport is a phone, where the bare route is the drill-down's list screen.
 */
export function firstRowToOpen(
  routed: string | null,
  rows: readonly string[],
  settled: boolean,
  mobile: boolean,
): string | null {
  if (routed !== null || !settled || mobile) return null;
  return rows[0] ?? null;
}
