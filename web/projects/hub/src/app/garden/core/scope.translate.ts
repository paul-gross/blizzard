// bzh:frontend-domain-boundary — the one place a scope's wire is read and its description edit written and refused.
import type { RefusalMap, RoutineView, RunRowView, ScopeView } from 'fleet';

import type { Scope, ScopeDescriptionDraft, ScopeRoutine, ScopeSweep } from './scope.domain';

/** A scope as the domain holds it. A project's name falls back to its slug, so a chip never shows a bare id. */
export function fromScopeView(wire: ScopeView): Scope {
  return {
    slug: wire.slug,
    description: wire.description,
    retired: wire.retired ?? false,
    projectId: wire.project_id ?? null,
    projectName: wire.project_name ?? wire.project_slug ?? null,
  };
}

/** A routine as a scope's panel names it. */
export function fromRoutineView(wire: RoutineView): ScopeRoutine {
  return {
    routineId: wire.routine_id,
    slug: wire.slug,
    name: wire.name,
    defaultScopeSlug: wire.default_scope_slug ?? null,
  };
}

/** A run as the sweep of its scope. */
export function toScopeSweep(wire: RunRowView): ScopeSweep {
  return { scopeSlug: wire.scope_slug, mintedAt: wire.minted_at };
}

/** What a description edit is sent as: the trimmed description. */
export interface ScopeDescriptionRequest {
  readonly description: string;
}

/** A draft as the edit request. */
export function toScopeDescriptionRequest(draft: ScopeDescriptionDraft): ScopeDescriptionRequest {
  return { description: draft.description.trim() };
}

/** How the hub's refusals of a description edit reach the draft: its own field on the field, anything else on the form
 * line. */
export const SCOPE_DESCRIPTION_REFUSALS: RefusalMap<ScopeDescriptionDraft> = {
  field: (path) => (path === 'description' ? 'description' : null),
  kinds: {},
};
