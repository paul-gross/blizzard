import { disabled, hidden, type LogicFn, required, schema, validate } from '@angular/forms/signals';

import { DECLINE_REASON_REQUIRED, isDeclineReasonMissing, type ProposalAcceptanceDraft } from './proposal-acceptance.domain';

/**
 * The one schema the Accept dialog is judged by, from the draft alone. A decline requires a reason, and whitespace
 * alone counts as blank; a mint leaves it optional. Body and urgency concern only a mint, so a decline disables and
 * hides them.
 */
export const proposalAcceptanceRules = schema<ProposalAcceptanceDraft>((p) => {
  const declining: LogicFn<string, boolean> = ({ valueOf }) => valueOf(p.mode) === 'decline';

  // `required` marks the control and judges the empty reason; `validate` adds the whitespace-only one.
  required(p.reason, { message: DECLINE_REASON_REQUIRED, when: declining });
  validate(p.reason, ({ value, valueOf }) =>
    value() !== '' && isDeclineReasonMissing({ mode: valueOf(p.mode), reason: value() })
      ? { kind: 'required', message: DECLINE_REASON_REQUIRED }
      : null,
  );

  disabled(p.body, declining);
  hidden(p.body, declining);
  disabled(p.urgency, declining);
  hidden(p.urgency, declining);
});
