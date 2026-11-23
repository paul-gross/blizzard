import type { RoutineBaseline } from '../core/run.domain';
import type { Scope } from '../core/scope.domain';

/** The routine's related, non-retired scopes, in the order `scopes` lists them — a
 * retired scope stays related but is offered to no run. */
export function liveRelatedScopes(
  scopes: readonly Scope[],
  relatedSlugs: ReadonlySet<string>,
): readonly Scope[] {
  return scopes.filter((s) => !s.retired && relatedSlugs.has(s.slug));
}

/** The scope slugs the routine has a sweep baseline for. */
export function sweptScopeSlugs(baselines: readonly RoutineBaseline[]): ReadonlySet<string> {
  return new Set(baselines.map((b) => b.scopeSlug));
}

/** `liveScopes` with the previously-swept ones first, in `baselines` order
 * (newest-swept first), and every other one after, in its own order. A baseline for a
 * scope that is no longer live is dropped. */
export function orderScopesBySwept(
  liveScopes: readonly Scope[],
  baselines: readonly RoutineBaseline[],
  swept: ReadonlySet<string>,
): readonly Scope[] {
  const bySlug = new Map(liveScopes.map((s) => [s.slug, s]));
  const sweptOrdered = baselines
    .map((b) => bySlug.get(b.scopeSlug))
    .filter((s): s is Scope => s !== undefined);
  const rest = liveScopes.filter((s) => !swept.has(s.slug));
  return [...sweptOrdered, ...rest];
}

/** The baseline the routine holds for `scope`, or `undefined` for a never-swept scope or none chosen yet. */
export function baselineForScope(
  baselines: readonly RoutineBaseline[],
  scope: string,
): RoutineBaseline | undefined {
  return baselines.find((b) => b.scopeSlug === scope);
}
