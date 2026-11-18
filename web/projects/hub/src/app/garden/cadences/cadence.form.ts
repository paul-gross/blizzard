import { apply, required, schema, validate } from '@angular/forms/signals';

import type { CadenceDraft } from '../core/cadence.domain';

const fiveFields = (cron: string): boolean => cron.trim().split(/\s+/).length === 5;

// The frontend's own rules for a cadence (`product:immediate-validation`). Those the hub also enforces are written again
// here on purpose; whether a slug is taken is left to the hub.
const editRules = schema<CadenceDraft>((p) => {
  validate(p.cron, ({ value }) =>
    value().trim() === '' || fiveFields(value())
      ? null
      : { kind: 'cron', message: 'Needs five fields: minute hour day month weekday' },
  );
  validate(p.spacing, ({ value }) =>
    value().trim() === '' || /^[1-9]\d*[mhd]$/.test(value().trim())
      ? null
      : { kind: 'spacing', message: 'A whole number above zero and m, h, or d' },
  );
  // The cross-field rules the hub states, judged here from the draft alone.
  validate(p.zone, ({ value, valueOf }) =>
    (value().trim() === '') === (valueOf(p.cron).trim() === '')
      ? null
      : { kind: 'schedule', message: 'A schedule needs both a cron and a zone' },
  );
  validate(p.anchor, ({ value, valueOf }) =>
    value() !== 'unstated' && valueOf(p.spacing).trim() === ''
      ? { kind: 'anchor', message: 'An anchor needs a spacing' }
      : null,
  );
  validate(p, ({ value }) =>
    value().cron.trim() === '' && value().spacing.trim() === ''
      ? { kind: 'cadence', message: 'State a schedule, a spacing, or both' }
      : null,
  );
});

/** A cadence edit's rules: the slug is fixed, so it is not judged. */
export const cadenceEditRules = editRules;

/** A cadence create's rules: an edit's, and a slug. */
export const cadenceCreateRules = schema<CadenceDraft>((p) => {
  apply(p, editRules);
  required(p.slug);
});
