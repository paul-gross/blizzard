import { required, schema } from '@angular/forms/signals';

import type { RoutineCreateDraft } from '../core/routine.domain';

// The frontend's own rules for a routine (`decision:ui-toolkit/validate-both`): the three fields the hub cannot do
// without. Whether a slug is taken, a graph resolves, or a scope slug is well formed is left to the hub.
export const routineCreateRules = schema<RoutineCreateDraft>((p) => {
  required(p.slug);
  required(p.graph);
  required(p.scope);
});
