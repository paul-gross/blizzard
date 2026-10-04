import type { RoutineView, ScopeView } from 'fleet';
import type { ScopeLifecycleVars } from './scope-lifecycle.mutations';
import type { RelatedRoutineVm, ScopePanelVm } from './scope-panel';

/** The scope `slug` names, or `null` when `slug` is `null` (the bare child route) or
 * names no loaded scope. */
export function scopeBySlug(slug: string | null, scopes: readonly ScopeView[]): ScopeView | null {
  return slug === null ? null : (scopes.find((s) => s.slug === slug) ?? null);
}

/** The scope's related routines, each resolved to its name (its id when the routine
 * read does not carry it) and marked whether this scope is that routine's own default —
 * `null` until both the related-routines read and the routine read resolve, or with no
 * scope. */
export function relatedRoutineRows(
  ids: readonly string[] | undefined,
  scope: ScopeView | null,
  routines: readonly RoutineView[] | undefined,
): readonly RelatedRoutineVm[] | null {
  if (ids === undefined || scope === null || routines === undefined) return null;
  const routinesById = new Map(routines.map((r) => [r.routine_id, r]));
  return ids.map((id) => {
    const routine = routinesById.get(id);
    return { name: routine?.name ?? id, isDefault: routine?.default_scope_slug === scope.slug };
  });
}

/** `scope`'s `retired` flag as it will read once a pending Retire/Enable settles
 * (`bzh:frontend-pending-override`) — `null` with no scope or nothing pending for it. */
export function scopeOverrideRetired(scope: ScopeView | null, pending: readonly ScopeLifecycleVars[]): boolean | null {
  if (scope === null) return null;
  const vars = pending.find((v) => v.slug === scope.slug);
  return vars ? vars.retired : null;
}

/** The scope panel's view model — `null` with no scope selected. `renderedRetired`
 * reads the pending override while one names this scope, else its real flag. */
export function scopePanelVm(
  scope: ScopeView | null,
  overrideRetired: boolean | null,
  relatedRoutines: readonly RelatedRoutineVm[] | null,
): ScopePanelVm | null {
  if (scope === null) return null;
  const real = scope.retired ?? false;
  return {
    slug: scope.slug,
    description: scope.description,
    retired: real,
    renderedRetired: overrideRetired ?? real,
    relatedRoutines,
  };
}
