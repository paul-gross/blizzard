/** The hub/runner's `{"detail": "..."}` error body, or anything close enough to read one
 * off of — 404/409 aren't in every generated error union (only 422 is documented in some),
 * so this reads the same shape defensively rather than trusting the response type. The one
 * owner of that read: every mutation's `onError` across both apps folds its failure through
 * this rather than typing its own copy (`chunk-detail.ts`'s pause/resume/detach/edit, the
 * runner top bar's local pause toggle). `fallback` names the verb that failed, for the case
 * where no body can be read. */
export function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'detail' in error && typeof error.detail === 'string') {
    return error.detail;
  }
  return fallback;
}
