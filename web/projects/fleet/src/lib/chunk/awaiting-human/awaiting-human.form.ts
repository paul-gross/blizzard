import { required, schema, validate } from '@angular/forms/signals';

import type { AnswerDraft } from './awaiting-human.domain';

const REQUIRED = { message: 'Required' };

/** The one rule an answer is judged by: it is required, and whitespace alone counts as blank. A gate's note has none. */
export const answerRules = schema<AnswerDraft>((p) => {
  required(p.answer, REQUIRED);
  validate(p.answer, ({ value }) =>
    value() !== '' && value().trim() === '' ? { kind: 'required', ...REQUIRED } : null,
  );
});
