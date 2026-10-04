import type { ScopeView } from 'fleet';
import type { ScopeRowVm } from './scope-list';

/** The effective selection: `scopeSlug` while it still names a loaded scope, else
 * `null` — so a stale or mistyped route param highlights no row. */
export function presentScopeSlug(scopeSlug: string | null, scopes: readonly ScopeView[]): string | null {
  if (scopeSlug === null) return null;
  return scopes.some((s) => s.slug === scopeSlug) ? scopeSlug : null;
}

/** The scope list's rows. */
export function scopeRows(scopes: readonly ScopeView[]): readonly ScopeRowVm[] {
  return scopes.map((s) => ({ slug: s.slug, description: s.description, retired: s.retired ?? false }));
}
