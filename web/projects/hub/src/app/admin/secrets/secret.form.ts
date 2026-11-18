import { required, schema, validate } from '@angular/forms/signals';

import type { SecretCreateDraft, SecretReplaceDraft } from './secret.domain';

// The frontend's own rules for a secret (`product:immediate-validation`), the hub's own requirements written again on
// purpose (`decision:ui-toolkit/validate-both`). Whether a name is taken is left to the hub.
/** The create form's rules: a name and a value. */
export const secretCreateRules = schema<SecretCreateDraft>((p) => {
  required(p.name, { message: 'A name is required' });
  // Whitespace alone is as empty as nothing: the translator trims the name it sends.
  validate(p.name, ({ value }) =>
    value() !== '' && value().trim() === '' ? { kind: 'required', message: 'A name is required' } : null,
  );
  required(p.value, { message: 'A value is required' });
});

/** The value replacement's rules: a value. */
export const secretReplaceRules = schema<SecretReplaceDraft>((p) => {
  required(p.value, { message: 'A value is required' });
});
