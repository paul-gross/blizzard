// exemplar: bzh:frontend-edit-baseline — the rules the form judges its draft by, from the draft alone.

import { disabled, hidden, type LogicFn, required, schema, validate } from '@angular/forms/signals';

import type { ProposalAcceptanceDraft } from './proposal-acceptance.domain';

const REQUIRED = { message: 'Required' };

/**
 * The one schema the Accept dialog is judged by, from the draft alone. A decline requires a reason, and whitespace
 * alone counts as blank; a mint leaves it optional. Body and urgency concern only a mint, so a decline disables and
 * hides them.
 */
export const proposalAcceptanceRules = schema<ProposalAcceptanceDraft>((p) => {
  const declining: LogicFn<string, boolean> = ({ valueOf }) => valueOf(p.mode) === 'decline';

  required(p.reason, { ...REQUIRED, when: declining });
  validate(p.reason, (ctx) =>
    declining(ctx) && ctx.value() !== '' && ctx.value().trim() === '' ? { kind: 'required', ...REQUIRED } : null,
  );

  disabled(p.body, declining);
  hidden(p.body, declining);
  disabled(p.urgency, declining);
  hidden(p.urgency, declining);
});
