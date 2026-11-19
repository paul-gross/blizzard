import type { Scope, ScopeRoutine } from '../core/scope.domain';
import type { ScopeLifecycleVars } from './scope-lifecycle.mutations';
import type { ScopeSweptVm } from './scope-list';
import type { RelatedRoutineVm, ScopePanelVm } from './scope-panel';

/** The scope `slug` names, or `null` when `slug` is `null` (the bare child route) or
 * names no loaded scope. */
export function scopeBySlug(slug: string | null, scopes: readonly Scope[]): Scope | null {
  return slug === null ? null : (scopes.find((s) => s.slug === slug) ?? null);
}

/** The scope's related routines, each resolved to its name (its id when the routine
 * read does not carry it) and marked whether this scope is that routine's own default —
 * `null` until both the related-routines read and the routine read resolve, or with no
 * scope. */
export function relatedRoutineRows(
  ids: readonly string[] | undefined,
  scope: Scope | null,
  routines: readonly ScopeRoutine[] | undefined,
): readonly RelatedRoutineVm[] | null {
  if (ids === undefined || scope === null || routines === undefined) return null;
  const routinesById = new Map(routines.map((r) => [r.routineId, r]));
  return ids.map((id) => {
    const routine = routinesById.get(id);
    return { slug: routine?.slug ?? id, name: routine?.name ?? id, isDefault: routine?.defaultScopeSlug === scope.slug };
  });
}

/** `scope`'s `retired` flag as it will read once a pending Retire/Enable settles
 * (`bzh:frontend-pending-override`) — `null` with no scope or nothing pending for it. */
export function scopeOverrideRetired(scope: Scope | null, pending: readonly ScopeLifecycleVars[]): boolean | null {
  if (scope === null) return null;
  const vars = pending.find((v) => v.slug === scope.slug);
  return vars ? vars.retired : null;
}

/** The scope panel's view model — `null` with no scope selected. `renderedRetired`
 * reads the pending override while one names this scope, else its real flag. */
export function scopePanelVm(
  scope: Scope | null,
  overrideRetired: boolean | null,
  relatedRoutines: readonly RelatedRoutineVm[] | null,
  swept: ScopeSweptVm | null,
): ScopePanelVm | null {
  if (scope === null) return null;
  const real = scope.retired;
  return {
    slug: scope.slug,
    description: scope.description,
    retired: real,
    renderedRetired: overrideRetired ?? real,
    relatedRoutines,
    swept,
  };
}

/**
 * The names of the other projects holding a scope of `scope`'s slug, by name — a slug is unique only inside its
 * project, so these are different records sharing a name. Empty with no scope, or until the lensed project is read.
 */
export function sameSlugProjects(
  scope: Scope | null,
  projectId: string | null,
  tenantScopes: readonly Scope[],
): readonly string[] {
  if (scope === null || projectId === null) return [];
  const names = tenantScopes
    .filter((other) => other.slug === scope.slug && other.projectId !== null && other.projectId !== projectId)
    .map((other) => other.projectName ?? other.projectId ?? '');
  return [...new Set(names)].sort((a, b) => a.localeCompare(b));
}
