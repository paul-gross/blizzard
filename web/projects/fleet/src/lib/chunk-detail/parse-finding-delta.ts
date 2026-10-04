import type { AddFindingOp, FindingDelta, GoneFindingOp, ObservedFindingOp } from '../api/hub';

/**
 * Runtime validation of a delivered finding list against the generated `FindingDelta`.
 *
 * An unknown top-level key or an unknown key on one op is ignored rather than
 * rejected, so a field `FindingDelta` grows later does not turn every
 * already-delivered artifact into a fallback-to-raw. The one deliberate departure
 * from the wire model's own optionality is `findings` (defaulted to `[]` on the
 * hub): {@link parseFindingDelta} requires the key to be *present* (an empty array
 * is fine) because `findings` is the one field that actually discriminates a delta
 * from a routine's own survey-shaped JSON, which shares `scope`/`revisions`/
 * `measurement` but never carries a `findings` list.
 */

type FindingOp = FindingDelta['findings'][number];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function parseAddOp(value: Record<string, unknown>): AddFindingOp | null {
  if (typeof value['class'] !== 'string') return null;
  if (typeof value['locus'] !== 'string') return null;
  if (typeof value['summary'] !== 'string') return null;
  if (!isNullableString(value['introduced'] ?? null)) return null;
  if (!isNullableString(value['ref'] ?? null)) return null;
  return {
    op: 'add',
    class: value['class'],
    locus: value['locus'],
    summary: value['summary'],
    introduced: (value['introduced'] as string | null | undefined) ?? null,
    ref: (value['ref'] as string | null | undefined) ?? null,
  };
}

function parseObservedOp(value: Record<string, unknown>): ObservedFindingOp | null {
  if (typeof value['id'] !== 'string') return null;
  return { op: 'observed', id: value['id'] };
}

function parseGoneOp(value: Record<string, unknown>): GoneFindingOp | null {
  if (typeof value['id'] !== 'string') return null;
  if (typeof value['note'] !== 'string') return null;
  return { op: 'gone', id: value['id'], note: value['note'] };
}

/** One `findings` entry, discriminated on `op` exactly as the wire union is —
 * anything else (a fourth `op` value, a missing required field) fails the whole
 * entry, which fails the whole delta ({@link parseFindingDelta}). */
function parseFindingOp(value: unknown): FindingOp | null {
  if (!isRecord(value)) return null;
  switch (value['op']) {
    case 'add':
      return parseAddOp(value);
    case 'observed':
      return parseObservedOp(value);
    case 'gone':
      return parseGoneOp(value);
    default:
      return null;
  }
}

/**
 * Parses `raw` as JSON and validates it against {@link FindingDelta}, returning `null`
 * on a JSON syntax failure, a non-object top level, or any field that fails its
 * check below — never throwing, so a caller renders structurally on a match and
 * falls back to the artifact's own verbatim text on anything else, the same
 * unparseable-or-mismatched fallback `chunk-artifact-body.ts` already owed its
 * asset content before this existed.
 *
 * `scope` and `findings` (as an array — see the module doc comment on why `findings`
 * is required here despite defaulting to `[]` on the wire) are the two keys that
 * must be present; `revisions` and `measurement` validate when present and default
 * to `{}`/`null` when absent, matching the wire model's own defaults. One
 * `findings` entry failing its own op check fails the whole parse — a delta half
 * rendered structurally and half silently dropped is worse than the whole thing
 * falling back to raw.
 */
export function parseFindingDelta(raw: string): FindingDelta | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;

  if (typeof parsed['scope'] !== 'string') return null;
  const scope = parsed['scope'];

  if (!Array.isArray(parsed['findings'])) return null;
  const findings: FindingOp[] = [];
  for (const entry of parsed['findings']) {
    const op = parseFindingOp(entry);
    if (op === null) return null;
    findings.push(op);
  }

  let revisions: Record<string, string> = {};
  if (parsed['revisions'] !== undefined) {
    if (!isRecord(parsed['revisions'])) return null;
    for (const value of Object.values(parsed['revisions'])) {
      if (typeof value !== 'string') return null;
    }
    revisions = parsed['revisions'] as Record<string, string>;
  }

  let measurement: string | null = null;
  if (parsed['measurement'] !== undefined) {
    if (!isNullableString(parsed['measurement'])) return null;
    measurement = parsed['measurement'];
  }

  return { scope, revisions, measurement, findings };
}
