import type { HttpValidationError } from '../api/hub';

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

/** The hub/runner's `{"detail": "..."}` error body, or anything close enough to read one
 * off of — 404/409 aren't in every generated error union (only 422 is documented in some),
 * so this reads the same shape defensively rather than trusting the response type. A 422's
 * list-shaped `detail` (`HttpValidationError`) reads as its field-named messages. The one
 * owner of that read: every mutation's `onError` across both apps folds its failure through
 * this rather than typing its own copy (`chunk-detail.ts`'s pause/resume/detach/edit, the
 * runner top bar's local pause toggle). `fallback` names the verb that failed, for the case
 * where no body can be read. */
export function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    if (typeof error.detail === 'string') return error.detail;
    return validationMessage(error.detail) ?? fallback;
  }
  return fallback;
}
