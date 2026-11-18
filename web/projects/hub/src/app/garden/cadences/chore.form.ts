import { apply, required, schema, validate } from '@angular/forms/signals';

import type { ChoreDraft } from '../core/cadence.domain';

const SLUG = /^[a-z0-9-]+$/;

// The frontend's own rules for a chore (`decision:ui-toolkit/validate-both`). Those the hub also enforces are written
// again here on purpose; whether a key is already taken is left to the hub.
export const choreEditRules = schema<ChoreDraft>((p) => {
  required(p.routine);
  validate(p.scopes, ({ value }) => {
    const scopes = value().trim();
    if (scopes === '' || scopes === 'all') return null;
    return scopes.split(/[\s,]+/).every((s) => s === '' || SLUG.test(s))
      ? null
      : { kind: 'scopes', message: 'all, or scope slugs of letters, digits, and dashes' };
  });
});

/** A chore add's rules: an edit's, and a key. */
export const choreAddRules = schema<ChoreDraft>((p) => {
  apply(p, choreEditRules);
  required(p.key);
  validate(p.key, ({ value }) =>
    value().trim() === '' || SLUG.test(value().trim())
      ? null
      : { kind: 'key', message: 'Letters, digits, and dashes in lower case' },
  );
});
