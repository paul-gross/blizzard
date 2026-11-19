import { required, schema, validate } from '@angular/forms/signals';

import type { ScopeDescriptionDraft } from '../core/scope.domain';

/** The frontend's own rule for a scope's description (`decision:ui-toolkit/validate-both`): it is required, and blank
 * text is none. */
export const SCOPE_DESCRIPTION_RULES = schema<ScopeDescriptionDraft>((p) => {
  required(p.description, { message: 'Enter a description' });
  validate(p.description, ({ value }) =>
    value() !== '' && value().trim() === '' ? { kind: 'required', message: 'Enter a description' } : null,
  );
});
