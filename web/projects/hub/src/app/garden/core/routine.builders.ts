// The routine domain builders specs and stories build their inputs from — domain literals only, no wire, no Angular.
// The wire builder (`gardenRoutineView` in `fleet/testing`) stays for the translator, query, and mutation specs.
import { fixtureInstant } from 'fleet/testing';

import type { Routine, RoutineKeeper } from './routine.domain';

/** A live routine running graph `gardening` over scope `blizzard`, under no project, at revision 1. */
export function gardenRoutine(overrides: Pick<Routine, 'slug'> & Partial<Routine>): Routine {
  return {
    id: `rtn_${overrides.slug}`,
    revision: 1,
    name: overrides.slug,
    graph: 'gardening',
    defaultScope: 'blizzard',
    defaultUrgency: null,
    effectiveUrgency: null,
    defaultModel: [],
    defaultEffort: null,
    defaultHarnesses: [],
    projectId: null,
    projectName: null,
    projectSlug: null,
    retired: false,
    keptBy: [],
    createdAt: fixtureInstant(-7 * 24 * 3600),
    ...overrides,
  };
}

/** A chore `nightly` of cadence `evening` keeping a routine, with no next moment. */
export function gardenRoutineKeeper(overrides: Partial<RoutineKeeper> = {}): RoutineKeeper {
  return { cadenceSlug: 'evening', cadenceName: 'Evening', choreKey: 'nightly', nextMoment: null, ...overrides };
}
