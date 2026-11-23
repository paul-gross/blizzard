// The scope domain builder specs and stories build their inputs from — domain literals only, no wire, no Angular.
// The wire builder (`gardenScopeView` in `fleet/testing`) stays for the translator, query, and mutation specs.
import type { Scope } from './scope.domain';

/** A live scope under no project, described by its slug. */
export function gardenScope(overrides: Pick<Scope, 'slug'> & Partial<Scope>): Scope {
  return { description: overrides.slug, retired: false, projectId: null, projectName: null, ...overrides };
}
