/**
 * Runner display names — how the board and the runner panel name a runner outside
 * the runners list: its compact id, the separator, then its operator-chosen name
 * (`rn_01KX…ABF3` named `r-claude` → `R-ABF3.r-claude`). A name is not unique — two
 * runners may share one — so the compact id leads and keeps them apart. A view that
 * carries no name renders the compact id alone.
 *
 * This is the single owner of that rendering (`bzh:frontend-formatters`); the
 * runners list and the runner panel's own identity show the full id beside the name
 * instead.
 */

import { compactRef } from './compact-ref';

/** What joins a runner's compact id to its name in {@link runnerDisplayName}. */
export const RUNNER_NAME_SEPARATOR = '.';

/** `('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude')` → `R-ABF3.r-claude`; with no name, `compactRef(id)`. */
export function runnerDisplayName(runnerId: string, runnerName?: string | null): string {
  const ref = compactRef(runnerId);
  return runnerName ? `${ref}${RUNNER_NAME_SEPARATOR}${runnerName}` : ref;
}

/** A runner reference's tooltip — the display name a surface may clip, then the full id it compacts:
 * `R-ABF3.r-claude · rn_01KXKVVF1J3D6H6VYZ3XYNABF3`. */
export function runnerTitle(runnerId: string, runnerName?: string | null): string {
  return `${runnerDisplayName(runnerId, runnerName)} · ${runnerId}`;
}
