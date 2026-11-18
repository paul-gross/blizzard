import { required, schema, validate } from '@angular/forms/signals';

import type { ProposalPassDraft } from './proposal-pass.domain';

const REQUIRED = { message: 'Required' };

/** The one schema the Pass dialog is judged by: the reason is required, and whitespace alone counts as blank. */
export const proposalPassRules = schema<ProposalPassDraft>((p) => {
  required(p.reason, REQUIRED);
  validate(p.reason, ({ value }) =>
    value() !== '' && value().trim() === '' ? { kind: 'required', ...REQUIRED } : null,
  );
});
