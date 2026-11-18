import type { Problem, ValidationEntry } from '../../api/hub';
import { errorMessage } from '../error-message';
import type { RefusalMap, SaveOutcome } from './outcome';

const LOCATION_SEGMENTS = ['body', 'query', 'path'];

/** A validation entry's `loc` as one lookup path: a leading `body`/`query`/`path` segment dropped, string segments
 * joined with `.`, a number segment as `[n]`. `['body','chores',0,'key']` reads `chores[0].key`. */
function normalizePath(loc: ValidationEntry['loc']): string {
  const segments = Array.isArray(loc) ? loc : [];
  let path = '';
  segments.forEach((segment, index) => {
    if (index === 0 && typeof segment === 'string' && LOCATION_SEGMENTS.includes(segment)) return;
    if (typeof segment === 'number') path += `[${segment}]`;
    else path += path === '' ? segment : `.${segment}`;
  });
  return path;
}

function readEntries(detail: unknown): { path: string; msg: string }[] | null {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const entries: { path: string; msg: string }[] = [];
  for (const entry of detail as unknown[]) {
    if (typeof entry !== 'object' || entry === null) return null;
    const { loc, msg } = entry as ValidationEntry;
    if (typeof msg !== 'string' || msg === '') return null;
    entries.push({ path: normalizePath(loc), msg });
  }
  return entries;
}

function join(previous: string | undefined, next: string): string {
  return previous === undefined ? next : `${previous}; ${next}`;
}

/**
 * A hub or runner refusal as a `SaveOutcome` over a draft's fields: the one shared reader of the refusal shapes a
 * form sees.
 *
 * - A validation list sends each entry to the field `map.field` names for its path, or to the form line when none.
 *   Several messages on one target are joined.
 * - A `type` listed in `map.kinds` sends the refusal's message to that field.
 * - Anything else (a network failure, a 5xx, an unknown 4xx, a list it cannot read) is the form line, worded by
 *   `errorMessage` with `fallback`.
 */
export function readRefusal<D>(
  error: unknown,
  map: RefusalMap<D>,
  fallback = 'Save failed.',
): SaveOutcome<D> {
  const problem: Problem | null =
    typeof error === 'object' && error !== null && !Array.isArray(error) ? error : null;

  const entries = readEntries(problem?.detail);
  if (entries !== null) {
    const fields: Partial<Record<keyof D, string>> = {};
    let form: string | undefined;
    for (const { path, msg } of entries) {
      const key = map.field(path);
      if (key === null) form = join(form, path === '' ? msg : `${path}: ${msg}`);
      else fields[key] = join(fields[key], msg);
    }
    return { ok: false, fields, form: form ?? null };
  }

  const type = problem?.type;
  if (typeof type === 'string' && Object.hasOwn(map.kinds, type)) {
    const key = map.kinds[type];
    return { ok: false, fields: { [key]: errorMessage(error, fallback) } as Partial<Record<keyof D, string>>, form: null };
  }

  return { ok: false, fields: {}, form: errorMessage(error, fallback) };
}
