import { applyEach, hidden, required, schema, validate } from '@angular/forms/signals';
import { type CredentialDraft, POSITIVE_INTEGER_MESSAGE, readPositiveInteger } from './credential.domain';

export const credentialRules = schema<CredentialDraft>((p) => {
  required(p.slug); required(p.forge); required(p.binding); required(p.secret);
  applyEach(p.settings, (entry) => {
    hidden(entry.text, ({ valueOf }) => valueOf(entry.binding) !== valueOf(p.binding));
    validate(entry.text, ({ value, valueOf }) => {
      if (valueOf(entry.binding) !== valueOf(p.binding) || value().trim() === '') return null;
      return valueOf(entry.type).kind !== 'positive-integer' || readPositiveInteger(value()) !== null ? null : { kind: 'positive-integer', message: POSITIVE_INTEGER_MESSAGE };
    });
  });
});
