import { disabled, hidden, required, schema, validate } from '@angular/forms/signals';

import { draftParks, PARK_TIMEOUT_MESSAGE, readParkTimeoutHours, type RepositoryDraft } from './repository.domain';

/**
 * The rules both repository forms judge a draft by (the slug may be renamed on edit, so it is required in both), the
 * hub's own written again here. Forge, owner, repository, base
 * branch, and credential are required; name and landing policy are not. The park timeout is hidden unless the policy
 * parks, disabled while the default is chosen, and otherwise whole hours from 1.
 */
export const repositoryRules = schema<RepositoryDraft>((p) => {
  required(p.slug);
  required(p.forge);
  required(p.owner);
  required(p.repo);
  required(p.baseBranch);
  required(p.credential);
  hidden(p.parkTimeoutHours, ({ valueOf }) => !draftParks({ landingPolicy: valueOf(p.landingPolicy) }));
  disabled(p.parkTimeoutHours, ({ valueOf }) => valueOf(p.parkTimeoutDefault));
  validate(p.parkTimeoutHours, ({ value, valueOf }) =>
    !draftParks({ landingPolicy: valueOf(p.landingPolicy) }) ||
    valueOf(p.parkTimeoutDefault) ||
    readParkTimeoutHours(value()) !== null
      ? null
      : { kind: 'hours', message: PARK_TIMEOUT_MESSAGE },
  );
});
