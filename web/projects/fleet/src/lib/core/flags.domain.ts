/** The feature switches and the names explicitly overridden for this page. */
export interface Flags {
  readonly flags: Readonly<Record<string, boolean>>;
  readonly overridden: readonly string[];
}
