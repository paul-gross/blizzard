/** How many leading characters of a commit hash a short hash keeps — git's own abbreviated length. */
export const SHORT_HASH_LENGTH = 7;

/** A commit hash shortened for display (`9f2c4e1a7b…` → `9f2c4e1`) — the single owner of that rendering. */
export function shortCommitHash(hash: string): string {
  return hash.slice(0, SHORT_HASH_LENGTH);
}
