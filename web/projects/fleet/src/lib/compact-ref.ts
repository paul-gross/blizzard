/**
 * Compact refs — the human-readable display name for a prefixed-ULID id
 * (`ch_01KX…3YJ9` → `C-3YJ9`, `lease_01KX…ZPRR` → `L-ZPRR`).
 *
 * A raw id is `{prefix}_{body}` (`foundation/ids.py`): a type prefix, an
 * underscore, and a 26-char ULID body. A compact ref is `{sigil}-{tail}` — the
 * entity kind's sigil joined to the tail of the ULID body. The ULID's timestamp
 * is at the front, so its entropy is in the tail: the last few characters
 * discriminate ids minted close together where a leading slice would show the
 * same timestamp on every row.
 *
 * This is the single owner of that rendering. Every surface that names an
 * entity compactly — board cards, the detail dock, the rail's ask list, the
 * runner's local panel — resolves through {@link compactRef} so they all agree.
 */

/** How one entity kind displays: its sigil and how much of the ULID body's tail to keep. */
export interface EntityDisplay {
  /** The one-or-few-character emblem for the entity kind (`ch` → `C`, `lease` → `L`). */
  readonly sigil: string;
  /** How many characters of the ULID body's tail the ref keeps. */
  readonly tailLength: number;
}

/**
 * The display registry, keyed by id prefix (`foundation/ids.py`'s vocabulary).
 * A prefix absent here falls back to its first letter, uppercased, with a
 * 4-char tail — so a new entity kind renders sanely before anyone registers it.
 * Register a prefix when the default collides (`ch`/`cho` both default to `C`)
 * or the kind deserves a distincter mark.
 */
export const ENTITY_DISPLAY: Readonly<Record<string, EntityDisplay>> = {
  ch: { sigil: 'C', tailLength: 4 },
  lease: { sigil: 'L', tailLength: 4 },
  qn: { sigil: 'Q', tailLength: 4 },
  tko: { sigil: 'T', tailLength: 4 },
  gr: { sigil: 'G', tailLength: 4 },
  nd: { sigil: 'N', tailLength: 4 },
  dec: { sigil: 'D', tailLength: 4 },
  cho: { sigil: 'CH', tailLength: 4 },
  art: { sigil: 'A', tailLength: 4 },
  tr: { sigil: 'TR', tailLength: 4 },
  self: { sigil: 'S', tailLength: 4 },
};

const DEFAULT_TAIL_LENGTH = 4;

/**
 * `ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9` → `C-3YJ9`. An id with no underscore (env
 * pool names like `e1`, `runner-local`) is not a prefixed ULID and passes
 * through unchanged — those names are already human-scale.
 */
export function compactRef(id: string): string {
  const sep = id.indexOf('_');
  if (sep <= 0) return id;
  const prefix = id.slice(0, sep);
  const body = id.slice(sep + 1);
  if (body.length === 0) return id;
  const display = ENTITY_DISPLAY[prefix] ?? {
    sigil: prefix[0].toUpperCase(),
    tailLength: DEFAULT_TAIL_LENGTH,
  };
  return `${display.sigil}-${body.slice(-display.tailLength)}`;
}
