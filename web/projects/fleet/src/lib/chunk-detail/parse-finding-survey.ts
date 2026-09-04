/**
 * A hand-written TypeScript mirror of the **survey** asset a garden routine's survey
 * node publishes — a JSON object carrying `scope`, `revisions`, `measurement`, and
 * `candidates`, each candidate a `FindingCandidate` from `src/blizzard/wire/finding.py`.
 *
 * Unlike {@link parseFindingDelta}'s subject, this shape has **no server-side parse at
 * all**: the delta is validated at delivery (`garden_delivery.py`'s `parse_delta`), but
 * the survey is an intra-run handoff — the survey node writes it so the reconcile
 * session, which enters cold, can read what that session saw. Its declaration is the
 * survey prompt itself (`src/blizzard/hub/graphs/garden-routine/prompts/survey.md`)
 * plus the `FindingCandidate` model, and nothing regenerates this file when either
 * changes.
 *
 * So the same drift discipline {@link parseFindingDelta} follows applies harder here:
 * every field below is what the prompt actually asks for and no more, and an unknown
 * key — top level or on a candidate — is ignored rather than rejected, so a field the
 * shape grows later does not turn every already-published survey into a
 * fallback-to-raw.
 *
 * `candidates` is the key that discriminates a survey from the delta it is published
 * alongside: the two share `scope`/`revisions`/`measurement` exactly, and differ only
 * in that a delta carries `findings` (op-tagged) and a survey carries `candidates`
 * (identity-less, since the hub mints the `fin_` id at delivery, never the run).
 */

/** Mirrors `FindingCandidate` — a survey entry, which carries no `fin_` id because
 * identity is minted at delivery. `ref` is stable only within its own submission, so
 * a later node in the same run can name it. */
export interface FindingSurveyCandidate {
  readonly ref: string | null;
  readonly class: string;
  readonly locus: string;
  readonly summary: string;
  readonly introduced: string | null;
}

/** The survey asset itself — the same head as a delta, with `candidates` in place of
 * the delta's op-tagged `findings`. */
export interface FindingSurvey {
  readonly scope: string;
  readonly revisions: Readonly<Record<string, string>>;
  readonly measurement: string | null;
  readonly candidates: readonly FindingSurveyCandidate[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

/** One `candidates` entry. `class`, `locus`, and `summary` are the three the prompt
 * requires of every candidate; `ref` and `introduced` are best-effort there ("omit
 * rather than guess"), so both default to `null` when absent. */
function parseCandidate(value: unknown): FindingSurveyCandidate | null {
  if (!isRecord(value)) return null;
  if (typeof value['class'] !== 'string') return null;
  if (typeof value['locus'] !== 'string') return null;
  if (typeof value['summary'] !== 'string') return null;
  if (!isNullableString(value['ref'] ?? null)) return null;
  if (!isNullableString(value['introduced'] ?? null)) return null;
  return {
    ref: (value['ref'] as string | null | undefined) ?? null,
    class: value['class'],
    locus: value['locus'],
    summary: value['summary'],
    introduced: (value['introduced'] as string | null | undefined) ?? null,
  };
}

/**
 * Parses `raw` as JSON and validates it against {@link FindingSurvey}, returning
 * `null` on a JSON syntax failure, a non-object top level, or any field that fails
 * its check — never throwing, so a caller renders structurally on a match and falls
 * back to the artifact's own verbatim text on anything else.
 *
 * `scope` and `candidates` (as an array) are the two keys that must be present;
 * `revisions` and `measurement` validate when present and default to `{}`/`null`.
 * One candidate failing its own check fails the whole parse, for the same reason
 * {@link parseFindingDelta} refuses a half-good delta: a list half rendered
 * structurally and half silently dropped is worse than the whole thing falling back
 * to raw.
 */
export function parseFindingSurvey(raw: string): FindingSurvey | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;

  if (typeof parsed['scope'] !== 'string') return null;
  const scope = parsed['scope'];

  if (!Array.isArray(parsed['candidates'])) return null;
  const candidates: FindingSurveyCandidate[] = [];
  for (const entry of parsed['candidates']) {
    const candidate = parseCandidate(entry);
    if (candidate === null) return null;
    candidates.push(candidate);
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

  return { scope, revisions, measurement, candidates };
}
