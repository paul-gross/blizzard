/** The gardening doorway's URL — the tab on All. */
export const GARDENING_DOORWAY_URL = '/gardening';

/** The tab a project's garden lands on when its URL names none: the proposals waiting on a person. */
export const GARDENING_LANDING_TAB = 'proposals';

/**
 * The project a garden call that names a record by slug or name is addressed in: the lens's. A slug or a name is unique
 * only inside its project, so such a call is never sent on All — the hub would refuse it for a tenant holding several
 * projects, and the board never picks one for it.
 */
export function gardenProject(lens: string | null): string {
  if (lens === null) throw new Error('A scope or routine is named only inside its project: choose a project first.');
  return lens;
}
