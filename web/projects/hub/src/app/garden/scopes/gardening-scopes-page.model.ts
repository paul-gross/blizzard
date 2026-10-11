import type { RunRowView, ScopeView } from 'fleet';
import type { ScopeRowVm } from './scope-list';

/** The effective selection: `scopeSlug` while it still names a loaded scope, else
 * `null` — so a stale or mistyped route param highlights no row. */
export function presentScopeSlug(scopeSlug: string | null, scopes: readonly ScopeView[]): string | null {
  if (scopeSlug === null) return null;
  return scopes.some((s) => s.slug === scopeSlug) ? scopeSlug : null;
}

/**
 * The scope list's rows: each scope's slug, description and when a run last swept it — the newest `minted_at` among
 * `runs` naming the scope, `null` when none does.
 */
export function scopeRows(scopes: readonly ScopeView[], runs: readonly RunRowView[]): readonly ScopeRowVm[] {
  const lastSwept = new Map<string, string>();
  for (const run of runs) {
    const seen = lastSwept.get(run.scope_slug);
    if (seen === undefined || Date.parse(run.minted_at) > Date.parse(seen)) lastSwept.set(run.scope_slug, run.minted_at);
  }
  return scopes.map((s) => ({
    slug: s.slug,
    description: s.description,
    retired: s.retired ?? false,
    lastSwept: lastSwept.get(s.slug) ?? null,
  }));
}
