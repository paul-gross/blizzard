/**
 * A lookup over a wire vocabulary that a newer server may extend (`bzh:frontend-open-vocabulary`).
 * Declared exhaustive — the table is a full `Record` over the generated vocabulary, so a value the
 * client learns fails to compile until it is placed — and total when called: a raw value the
 * table does not hold resolves through `fallback` rather than to `undefined`.
 */
export interface OpenLookup<K extends string, V> {
  (value: string): V;
  /** The declared vocabulary, in the table's own order. */
  readonly keys: readonly K[];
  /** Whether `value` is one of the declared values. */
  has(value: string): value is K;
}

/** Builds an {@link OpenLookup} from `table`, resolving any value outside it through `fallback(raw)`. */
export function openLookup<K extends string, V>(table: Readonly<Record<K, V>>, fallback: (raw: string) => V): OpenLookup<K, V> {
  const has = (value: string): value is K => Object.hasOwn(table, value);
  const lookup = (value: string): V => (has(value) ? table[value] : fallback(value));
  return Object.assign(lookup, { keys: Object.keys(table) as K[], has });
}

/**
 * The `default` of a `switch` that gives every declared value its own `case`: the switched value,
 * raw. Its parameter is typed `never`, so a value the client learns fails to compile here until it
 * is given a case — the exhaustiveness a `default` branch would otherwise silence — while a value
 * a newer server sends still arrives at run time, as itself.
 */
export function unlisted(value: never): string {
  return String(value);
}
