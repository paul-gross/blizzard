/** How a save ended, for a form: it either succeeded, or was refused with a message per draft field and an
 * optional form-level line for what no field owns. A service answers this for every refusal and never throws one. */
export type SaveOutcome<D> =
  | { readonly ok: true }
  | {
      readonly ok: false;
      readonly fields: Partial<Record<keyof D, string>>;
      readonly form: string | null;
    };

/** How one concept's refusals reach its draft fields. */
export interface RefusalMap<D> {
  /** A hub field path, normalized (see `readRefusal`), to the draft field it concerns; null when none does. */
  readonly field: (path: string) => keyof D | null;
  /** A problem `type` that concerns one field though it carries no path, such as a slug already taken. */
  readonly kinds: Readonly<Record<string, keyof D>>;
}
