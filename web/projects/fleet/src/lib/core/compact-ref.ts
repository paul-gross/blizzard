/**
 * Compact refs — the human-readable display name for a prefixed-ULID id
 * (`ch_01KX…3YJ9` → `C-3YJ9`, `lease_01KX…ZPRR` → `L-ZPRR`).
 *
 * A raw id is `{prefix}_{body}`: an {@link IdPrefix} type prefix, an
 * underscore, and a 26-char ULID body. A compact ref is `{sigil}-{tail}` — the
 * entity kind's sigil joined to the tail of the ULID body. The ULID's timestamp
 * is at the front, so its entropy is in the tail: the last few characters
 * discriminate ids minted close together where a leading slice would show the
 * same timestamp on every row.
 *
 * This is the single owner of that rendering, through {@link compactRef}.
 */

import type { IdPrefix } from '../api/hub';
import { openLookup } from './open-lookup';

/** How one entity kind displays: its sigil and how much of the ULID body's tail to keep. */
export interface EntityDisplay {
  /** The one-or-few-character emblem for the entity kind (`ch` → `C`, `lease` → `L`). */
  readonly sigil: string;
  /** How many characters of the ULID body's tail the ref keeps. */
  readonly tailLength: number;
}

const DEFAULT_TAIL_LENGTH = 4;

/** The default display: the prefix's first letter, uppercased, with a 4-char tail. */
function defaultDisplay(prefix: string): EntityDisplay {
  return { sigil: prefix[0].toUpperCase(), tailLength: DEFAULT_TAIL_LENGTH };
}

/**
 * The display registry, keyed by the wire's {@link IdPrefix} vocabulary. Every declared prefix
 * has an entry; most are the default (first letter, uppercased, 4-char tail), and a prefix
 * outside the vocabulary falls back to that same default — so a new entity kind renders sanely
 * before anyone registers it.
 * Give a prefix its own mark when the default collides (`ch`/`cho` both default to `C`)
 * or the kind deserves a distincter mark.
 */
const ENTITY_DISPLAY = openLookup<IdPrefix, EntityDisplay>(
  {
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
    fin: { sigil: 'F', tailLength: 4 },
    /* `fins` (a finding set) would default to the same `F` as `fin` (a finding) above. */
    fins: { sigil: 'FS', tailLength: 4 },
    /* `gprop` (a garden proposal) would default to `G`, colliding with `gr` (graphs). */
    gprop: { sigil: 'GP', tailLength: 4 },
    /* `rtn` (a routine) and `rn` (a runner) would both default to `R`; the runner keeps the bare letter. */
    rtn: { sigil: 'RTN', tailLength: 4 },
    rn: { sigil: 'R', tailLength: 4 },
    /* `proj` (a project) would default to `P`; the full word reads as a project at a glance. */
    proj: { sigil: 'PROJ', tailLength: 4 },
    hs: { sigil: 'H', tailLength: 4 },
    mg: defaultDisplay('mg'),
    usr: defaultDisplay('usr'),
    seg: defaultDisplay('seg'),
    wi: defaultDisplay('wi'),
    scp: defaultDisplay('scp'),
    dep: defaultDisplay('dep'),
    apl: defaultDisplay('apl'),
    ten: defaultDisplay('ten'),
    inv: defaultDisplay('inv'),
    repo: defaultDisplay('repo'),
    wsrc: defaultDisplay('wsrc'),
    frg: defaultDisplay('frg'),
    flw: defaultDisplay('flw'),
    fsub: defaultDisplay('fsub'),
    htn: defaultDisplay('htn'),
  },
  defaultDisplay,
);

/**
 * `ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9` → `C-3YJ9`. An id with no underscore (env
 * pool names like `e1`) is not a prefixed ULID and passes
 * through unchanged — those names are already human-scale.
 */
export function compactRef(id: string): string {
  const sep = id.indexOf('_');
  if (sep <= 0) return id;
  const prefix = id.slice(0, sep);
  const body = id.slice(sep + 1);
  if (body.length === 0) return id;
  const display = ENTITY_DISPLAY(prefix);
  return `${display.sigil}-${body.slice(-display.tailLength)}`;
}
