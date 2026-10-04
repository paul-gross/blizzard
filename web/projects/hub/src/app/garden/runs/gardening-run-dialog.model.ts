import type { RoutineBaselineView, ScopeView } from 'fleet';

/** The routine's related, non-retired scopes, in the order `scopes` lists them — a
 * retired scope stays related but is offered to no run. */
export function liveRelatedScopes(
  scopes: readonly ScopeView[],
  relatedSlugs: ReadonlySet<string>,
): readonly ScopeView[] {
  return scopes.filter((s) => !s.retired && relatedSlugs.has(s.slug));
}

/** The scope slugs the routine has a sweep baseline for. */
export function sweptScopeSlugs(baselines: readonly RoutineBaselineView[]): ReadonlySet<string> {
  return new Set(baselines.map((b) => b.scope_slug));
}

/** `liveScopes` with the previously-swept ones first, in `baselines` order
 * (newest-swept first), and every other one after, in its own order. A baseline for a
 * scope that is no longer live is dropped. */
export function orderScopesBySwept(
  liveScopes: readonly ScopeView[],
  baselines: readonly RoutineBaselineView[],
  swept: ReadonlySet<string>,
): readonly ScopeView[] {
  const bySlug = new Map(liveScopes.map((s) => [s.slug, s]));
  const sweptOrdered = baselines
    .map((b) => bySlug.get(b.scope_slug))
    .filter((s): s is ScopeView => s !== undefined);
  const rest = liveScopes.filter((s) => !swept.has(s.slug));
  return [...sweptOrdered, ...rest];
}
