import { apply, required, schema, validate } from '@angular/forms/signals';

import type { WorkSourceDraft } from './work-source.domain';

// The frontend's own rules for a work source (`product:immediate-validation`). Create and edit both require the same
// fields, the hub's own requirements written again on purpose (`decision:ui-toolkit/validate-both`). Whether a slug
// or locator is taken is left to the hub. The credential stays optional.
const rules = schema<WorkSourceDraft>((p) => {
  const requiredText = (field: typeof p.slug, message: string): void => {
    required(field, { message });
    // Whitespace alone is as empty as nothing: the translator trims what it sends.
    validate(field, ({ value }) => (value() !== '' && value().trim() === '' ? { kind: 'required', message } : null));
  };
  requiredText(p.slug, 'A slug is required');
  requiredText(p.provider, 'A provider is required');
  requiredText(p.locator, 'A locator is required');
  requiredText(p.forge, 'A forge is required');
});

/** A work source edit's rules. */
export const workSourceEditRules = rules;

/** A work source create's rules: the same as an edit's. */
export const workSourceCreateRules = schema<WorkSourceDraft>((p) => {
  apply(p, rules);
});
