import { apply, required, type SchemaPath, schema, validate } from '@angular/forms/signals';

import type { ForgeDraft } from './forge.domain';

const REQUIRED = { message: 'Required' };

/** A value that is only whitespace is blank; the built-in `required` already names the truly empty one. */
function blankText(value: string): boolean {
  return value !== '' && value.trim() === '';
}

function requiredText(p: SchemaPath<string>): void {
  required(p, REQUIRED);
  validate(p, ({ value }) => (blankText(value()) ? { kind: 'required', ...REQUIRED } : null));
}

// The frontend's own rules for a forge (`decision:ui-toolkit/validate-both`). The hub also enforces them, and refuses
// a blank name on an edit; the origin and slug grammar and what is already taken are left to the hub.
const sharedRules = schema<ForgeDraft>((p) => {
  requiredText(p.slug);
  requiredText(p.apiBase);
});

/** A forge create's rules: a slug, a kind, and an API base. */
export const forgeCreateRules = schema<ForgeDraft>((p) => {
  apply(p, sharedRules);
  requiredText(p.kind);
});

/** A forge edit's rules: a slug, a name, and an API base. The kind is fixed, so it is not judged. */
export const forgeEditRules = schema<ForgeDraft>((p) => {
  apply(p, sharedRules);
  requiredText(p.name);
});
