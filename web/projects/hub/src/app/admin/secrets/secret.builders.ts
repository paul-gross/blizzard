// The secret domain builders model and component specs build their inputs from — domain literals only, no wire, no
// Angular. The wire builder `secretView` (`fleet/testing`) stays for the translator and query specs and for answering a
// stubbed transport.
import type { Secret } from './secret.domain';

/** An enabled secret `gh-blizzard` at revision 3, referred to by one work source and one repository. */
export function secret(overrides: Partial<Secret> = {}): Secret {
  return {
    name: 'gh-blizzard',
    revision: 3,
    retired: false,
    retirable: false,
    replacedBy: 'pgross',
    replacedByName: null,
    references: [
      { kind: 'work_source', key: 'blizzard', label: null },
      { kind: 'repository', key: 'blizzard', label: null },
    ],
    ...overrides,
  };
}
