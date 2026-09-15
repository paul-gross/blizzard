/**
 * The limit every drained hub read passes explicitly (blizzard#526 D7) — the board's
 * counterpart to the CLI's own `CliContext.get_all`. No view truncates at any future
 * fleet size.
 */
export const DRAIN_LIMIT = 1000;

/**
 * Drains every page of a hub keyset-paginated list read (blizzard#526 D7).
 * `fetchPage` is called once per page with the previous page's `next_cursor`
 * (`undefined` on the first call); a page whose `next_cursor` is `null`/`undefined`
 * ends the drain, so a fleet within one page — today's size, on every endpoint this
 * wraps — issues exactly one request. An error on any page, not just the first,
 * surfaces as this call's own rejection, so a `queryFn` that awaits it carries a
 * mid-drain failure straight to the query's error state.
 */
export async function drainPages<Row, Page extends { next_cursor?: string | null }>(
  fetchPage: (cursor: string | undefined) => Promise<{ data?: Page; error: unknown }>,
  rowsOf: (page: Page) => Row[] | undefined,
): Promise<Row[]> {
  const rows: Row[] = [];
  let cursor: string | undefined;
  for (;;) {
    const { data, error } = await fetchPage(cursor);
    if (error) throw error;
    rows.push(...(rowsOf(data as Page) ?? []));
    const next = data?.next_cursor;
    if (next == null) return rows;
    cursor = next;
  }
}
