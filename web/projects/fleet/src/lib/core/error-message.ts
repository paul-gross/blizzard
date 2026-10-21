import type { HttpValidationError, ProjectRequiredBody } from '../api/hub';

/** FastAPI's list-shaped 422 `detail` as one message per entry, each named by the field
 * its `loc` points at (the leading `body`/`query` segment dropped), or `null` when
 * `detail` isn't that shape. */
function validationMessage(detail: unknown): string | null {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const messages: string[] = [];
  for (const entry of detail as NonNullable<HttpValidationError['detail']>) {
    if (!entry || typeof entry.msg !== 'string') return null;
    const loc = (Array.isArray(entry.loc) ? entry.loc : []).filter(
      (segment, index) => !(index === 0 && (segment === 'body' || segment === 'query' || segment === 'path')),
    );
    messages.push(loc.length > 0 ? `${loc.join('.')}: ${entry.msg}` : entry.msg);
  }
  return messages.join('; ');
}

/** A coded refusal's `detail` object — a `message`, and for a `ProjectRequiredBody` the
 * tenant's projects to choose from — as its message, or `null` when `detail` isn't that
 * shape. A message that comes with projects names each so the caller can name one. */
function refusalMessage(detail: unknown): string | null {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null;
  const { message, projects } = detail as Partial<ProjectRequiredBody>;
  if (typeof message !== 'string' || message === '') return null;
  if (!Array.isArray(projects) || projects.length === 0) return message;
  const named = projects
    .filter((project) => project && typeof project.slug === 'string')
    .map((project) => (project.name && project.name !== project.slug ? `${project.name} (${project.slug})` : project.slug));
  return named.length > 0 ? `${message} Projects: ${named.join(', ')}.` : message;
}

/** The hub/runner's `{"detail": "..."}` error body, or anything close enough to read one
 * off of — 404/409 aren't in every generated error union (only 422 is documented in some),
 * so this reads the same shape defensively rather than trusting the response type. A 422's
 * list-shaped `detail` (`HttpValidationError`) reads as its field-named messages. The one
 * owner of that read. A coded refusal's object `detail` reads as its
 * message — a `project_required` one with the projects it asks to choose from. `fallback`
 * names the verb that failed, for the case where no body can be read. */
export function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    if (typeof error.detail === 'string') return error.detail;
    return validationMessage(error.detail) ?? refusalMessage(error.detail) ?? fallback;
  }
  return fallback;
}
