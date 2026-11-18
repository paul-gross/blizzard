// exemplar: bzh:frontend-edit-baseline — the rules the form judges its draft by, the hub's own written again here.

import { schema, validate } from '@angular/forms/signals';

import type { CadenceDraft } from './cadence.domain';

/** A cadence edit's rules. */
export const cadenceEditRules = schema<CadenceDraft>((p) => {
  validate(p.cron, ({ value }) =>
    value().trim() === '' || value().trim().split(/\s+/).length === 5
      ? null
      : { kind: 'cron', message: 'Needs five fields: minute hour day month weekday' },
  );
  validate(p.spacing, ({ value }) =>
    value().trim() === '' || /^[1-9]\d*[mhd]$/.test(value().trim())
      ? null
      : { kind: 'spacing', message: 'A whole number above zero and m, h, or d' },
  );
  validate(p.zone, ({ value, valueOf }) =>
    (value().trim() === '') === (valueOf(p.cron).trim() === '')
      ? null
      : { kind: 'schedule', message: 'A schedule needs both a cron and a zone' },
  );
  validate(p, ({ value }) =>
    value().cron.trim() === '' && value().spacing.trim() === ''
      ? { kind: 'cadence', message: 'State a schedule, a spacing, or both' }
      : null,
  );
});
