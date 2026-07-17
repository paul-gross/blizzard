/**
 * A chunk's human-readable short name — `ch_…` plus the ULID's last four
 * characters (e.g. `ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9` → `ch_…3YJ9`). A ULID's entropy
 * is in its tail, so the last four discriminate the chunks on a board where a
 * leading slice would show the same timestamp prefix on every card. Until chunks
 * carry a real sequential name (e.g. `C-121`), this is how every
 * fleet view names a chunk.
 *
 * It lives here rather than in a view because three unrelated surfaces name chunks —
 * the board card, the detail dock, and the rail's ask list — and they must agree.
 */
export function shortChunkId(chunkId: string): string {
  return `ch_…${chunkId.slice(-4)}`;
}
