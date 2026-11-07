import type { Problem, ValidationEntry } from '../api/hub';

/** Whether `value` reads as a refusal body: an object, whose members are all optional because a hub
 * before problem details sends only `detail`. */
function isProblem(value: unknown): value is Problem {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isValidationEntry(
  entry: unknown,
): entry is ValidationEntry & { msg: string } {
  return (
    typeof entry === 'object' &&
    entry !== null &&
    'msg' in entry &&
    typeof entry.msg === 'string'
  );
}

/** FastAPI's list-shaped 422 `detail` as one message per entry, each named by the field
 * its `loc` points at (the leading `body`/`query` segment dropped), or `null` when
 * `detail` isn't that shape. */
function validationMessage(detail: unknown): string | null {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const messages: string[] = [];
  for (const entry of detail) {
    if (!isValidationEntry(entry)) return null;
    const loc = (Array.isArray(entry.loc) ? entry.loc : []).filter(
      (segment, index) =>
        !(
          index === 0 &&
          (segment === 'body' || segment === 'query' || segment === 'path')
        ),
    );
    messages.push(
      loc.length > 0 ? `${loc.join('.')}: ${entry.msg}` : entry.msg,
    );
  }
  return messages.join('; ');
}

/** The slug-or-name of each project a refusal offers, for a project-required refusal's `projects`. */
function projectNames(projects: unknown): string[] {
  if (!Array.isArray(projects)) return [];
  const names: string[] = [];
  for (const project of projects) {
    if (
      typeof project !== 'object' ||
      project === null ||
      !('slug' in project) ||
      typeof project.slug !== 'string'
    )
      continue;
    const name =
      'name' in project && typeof project.name === 'string' ? project.name : '';
    names.push(
      name && name !== project.slug
        ? `${name} (${project.slug})`
        : project.slug,
    );
  }
  return names;
}

/** A coded refusal's `detail` object — a `message`, and for a project-required refusal the
 * tenant's projects to choose from — as its message, or `null` when `detail` isn't that
 * shape. A message that comes with projects names each so the caller can name one. */
function refusalMessage(detail: unknown): string | null {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail))
    return null;
  const message = 'message' in detail ? detail.message : undefined;
  if (typeof message !== 'string' || message === '') return null;
  const named = 'projects' in detail ? projectNames(detail.projects) : [];
  return named.length > 0
    ? `${message} Projects: ${named.join(', ')}.`
    : message;
}

/** A refusal body's `type` when it carries one — what a verb that acts on one refusal compares with the registered
 * URN. Display never reads it. */
export function problemType(error: unknown): string | undefined {
  const type =
    typeof error === 'object' && error !== null
      ? (error as { type?: unknown }).type
      : undefined;
  return typeof type === 'string' ? type : undefined;
}

/** What the operator is shown for a hub or runner refusal: the problem's `detail` — a string, a
 * coded object's `message`, or a validation list's field-named messages — else its `title`. It
 * reads the generated `Problem` and never the `type`: a type the board does not know is shown by
 * its message. `fallback` names the verb that failed, for the case where no body can be read. The
 * one owner of that read. */
export function errorMessage(error: unknown, fallback: string): string {
  if (!isProblem(error)) return fallback;
  const { detail, title } = error;
  if (typeof detail === 'string') return detail;
  const fromDetail = validationMessage(detail) ?? refusalMessage(detail);
  if (fromDetail !== null) return fromDetail;
  return typeof title === 'string' && title !== '' ? title : fallback;
}
