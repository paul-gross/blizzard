// bzh:frontend-domain-boundary — a scope as the hub reads it and as its description form edits it; no wire, no Angular.

/** A scope as the hub reads it. A scope's slug names it only inside its project. */
export interface Scope {
  readonly slug: string;
  readonly description: string;
  readonly retired: boolean;
  /** The owning project's id; `null` when the hub names none. */
  readonly projectId: string | null;
  /** The owning project's name, else its slug; `null` when the hub names neither. */
  readonly projectName: string | null;
}

/** A scope's description as its inline editor states it, as typed. */
export interface ScopeDescriptionDraft {
  readonly description: string;
}

/** The draft the editor opens on a scope with: its current description. */
export function scopeDescriptionDraft(scope: { readonly description: string }): ScopeDescriptionDraft {
  return { description: scope.description };
}

/** The draft of no scope open. */
export const EMPTY_SCOPE_DESCRIPTION_DRAFT: ScopeDescriptionDraft = { description: '' };

/** A routine as a scope's panel names it: enough to link it and mark whether the scope is its default. */
export interface ScopeRoutine {
  readonly routineId: string;
  readonly slug: string;
  readonly name: string;
  /** The slug of the scope the routine runs on by default; `null` when it has none. */
  readonly defaultScopeSlug: string | null;
}

/** A run that swept a scope: which scope, and when the hub minted the run. */
export interface ScopeSweep {
  readonly scopeSlug: string;
  readonly mintedAt: string;
}
