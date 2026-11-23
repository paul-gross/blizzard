// bzh:frontend-open-vocabulary — how a garden concept holds a value from a vocabulary a newer hub may extend; no wire,
// no Angular.

/** A vocabulary value this build does not know, carried as the hub sent it so it renders, and resends, as itself. */
export interface Unlisted {
  readonly unlisted: string;
}

/** `raw`, a value this build does not know, held as an {@link Unlisted}. */
export function unlisted(raw: string): Unlisted {
  return { unlisted: raw };
}

/** A vocabulary value as the domain holds it: one this build knows, or an {@link Unlisted} raw value. */
export type Vocabulary<K extends string> = K | Unlisted;

/** The value `v` names, as the hub spells it — a known value itself, an unlisted one raw. */
export function vocabularyText<K extends string>(v: Vocabulary<K>): string {
  return typeof v === 'string' ? v : v.unlisted;
}
