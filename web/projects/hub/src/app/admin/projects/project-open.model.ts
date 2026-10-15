import { firstRowToOpen } from '../../core/first-row';

/** What the projects list knows when deciding which project to open on a bare `/admin/projects`. */
export interface ProjectToOpenInput {
  /** The project the URL names, `null` on the bare route. */
  readonly selected: string | null;
  /** The lensed project's id, `null` on All. */
  readonly lensId: string | null;
  /** The listed projects' ids, in list order. */
  readonly rows: readonly string[];
  /** Whether the projects read has answered. */
  readonly settled: boolean;
  readonly mobile: boolean;
}

/** The project to open when the URL names none: the lensed one under a lens, at every width; on All, the first
 * listed project on desktop, while a phone stays on the list. `null` when nothing should move. */
export function projectToOpen(input: ProjectToOpenInput): string | null {
  if (input.selected !== null) return null;
  if (input.lensId !== null) return input.lensId;
  return firstRowToOpen(input.selected, input.rows, input.settled, input.mobile);
}

/** The listed rows' keys, in list order. */
export function rowKeys(rows: readonly { readonly key: string }[]): readonly string[] {
  return rows.map((row) => row.key);
}
