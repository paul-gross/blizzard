/** How many leading characters of a revision sha a fact row or entry line shows — the
 * full sha stays reachable through the element's own `title` tooltip. Deliberately not
 * `compactRef`: that shortens a `{prefix}_{ULID}` id by its *tail*, where a ULID's
 * entropy lives; a git sha carries no prefix and its entropy is spread evenly, so a
 * leading slice reads the same as any other. */
const SHA_PREFIX_LENGTH = 10;

/** `sha` cut to {@link SHA_PREFIX_LENGTH}, ellipsed — returned whole when it is
 * already that short, so an abbreviated sha the run recorded by hand is not ellipsed
 * for nothing. */
export function shortSha(sha: string): string {
  return sha.length > SHA_PREFIX_LENGTH ? `${sha.slice(0, SHA_PREFIX_LENGTH)}…` : sha;
}
