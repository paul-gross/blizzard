import { hidden, required, schema, validate } from '@angular/forms/signals';

import type { FindingTriageDraft } from './finding-triage.domain';

const REQUIRED = { message: 'Required' };

/** A value that is only whitespace is blank; the built-in `required` already names the truly empty one. */
function blankText(value: string): boolean {
  return value !== '' && value.trim() === '';
}

/** The one schema every triage verb is judged by, from the draft alone: the note is always required, and the
 * absorbing finding's id only under `supersede` (hidden from validation otherwise). */
export const findingTriageRules = schema<FindingTriageDraft>((p) => {
  required(p.note, REQUIRED);
  validate(p.note, ({ value }) => (blankText(value()) ? { kind: 'required', ...REQUIRED } : null));

  hidden(p.supersededBy, ({ valueOf }) => valueOf(p.verb) !== 'supersede');
  required(p.supersededBy, { ...REQUIRED, when: ({ valueOf }) => valueOf(p.verb) === 'supersede' });
  validate(p.supersededBy, ({ value }) => (blankText(value()) ? { kind: 'required', ...REQUIRED } : null));
});
