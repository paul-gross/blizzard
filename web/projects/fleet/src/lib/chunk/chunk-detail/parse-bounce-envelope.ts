import type { BounceAction, BounceEnvelope, BounceFailedStep, BounceUnlandedRepo } from '../../api/hub';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseStep(value: unknown): BounceFailedStep | null | undefined {
  if (value === null || value === undefined) return null;
  if (!isRecord(value)) return undefined;
  const { name, exit_code, output_tail, log_artifact } = value;
  if (typeof name !== 'string' || typeof exit_code !== 'number') return undefined;
  if (typeof output_tail !== 'string' || typeof log_artifact !== 'string') return undefined;
  return { name, exit_code, output_tail, log_artifact };
}

function parseAction(value: unknown): BounceAction | null | undefined {
  if (value === null || value === undefined) return null;
  if (!isRecord(value)) return undefined;
  const { name, choice, log_artifact } = value;
  if (typeof name !== 'string' || typeof choice !== 'string') return undefined;
  if (log_artifact !== null && log_artifact !== undefined && typeof log_artifact !== 'string') return undefined;
  return { name, choice, log_artifact: log_artifact ?? null };
}

function parseUnlandedRepo(value: unknown): BounceUnlandedRepo | null {
  if (!isRecord(value)) return null;
  const { repo, branch, commit, pr } = value;
  if (typeof repo !== 'string' || typeof branch !== 'string' || typeof commit !== 'string') return null;
  if (pr === null || pr === undefined) return { repo, branch, commit, pr: null };
  if (!isRecord(pr) || typeof pr['number'] !== 'number' || typeof pr['url'] !== 'string') return null;
  return { repo, branch, commit, pr: { number: pr['number'], url: pr['url'] } };
}

/**
 * Runtime validation of the one-line JSON envelope the hub records on a refused delivery against the
 * generated `BounceEnvelope`. `null` when `raw` is not JSON, not an object, or any field fails its check — the
 * caller falls back to the artifact's own text. An unknown key is ignored. `unlanded` defaults to `[]`, and `step`
 * and `action` to `null`, when absent, so an envelope that names only a `cause` and a `detail` still reads
 * structurally — as does one stored before the hub named the `action`, which carries a `step` instead.
 */
export function parseBounceEnvelope(raw: string): BounceEnvelope | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  const { cause, detail } = parsed;
  if (typeof cause !== 'string' || typeof detail !== 'string') return null;

  const unlanded: BounceUnlandedRepo[] = [];
  if (parsed['unlanded'] !== undefined) {
    if (!Array.isArray(parsed['unlanded'])) return null;
    for (const entry of parsed['unlanded']) {
      const repo = parseUnlandedRepo(entry);
      if (repo === null) return null;
      unlanded.push(repo);
    }
  }

  const step = parseStep(parsed['step']);
  if (step === undefined) return null;
  const action = parseAction(parsed['action']);
  if (action === undefined) return null;
  return { cause, detail, unlanded, step, action };
}

/** The envelope as one readable line: its `detail` with markdown code ticks dropped, else
 * its `cause`, else a fixed word for an envelope that parses but names neither — never its JSON.
 * Unparseable text is a human reason already, so it stands as it is. Read leniently, apart from
 * {@link parseBounceEnvelope}: a history bounce's envelope is any JSON object naming a `cause`, a `detail`, or neither. */
export function bounceReason(raw: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return raw;
  }
  if (!isRecord(parsed)) return raw;
  const text = [parsed['detail'], parsed['cause']].find((field): field is string => typeof field === 'string' && field !== '');
  return text === undefined ? 'bounced' : text.replaceAll('`', '');
}
