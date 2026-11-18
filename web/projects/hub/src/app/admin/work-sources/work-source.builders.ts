// The work-source domain builders model and component specs build their inputs from — domain literals only, no wire,
// no Angular. The wire builder `workSourceSummary` (`fleet/testing`) stays for the translator and query specs and for
// answering a stubbed transport.
import type { WorkSource } from './work-source.domain';

/** An enabled Jira work source `jira` at its first revision, with no forge or credential. */
export function workSource(overrides: Partial<WorkSource> = {}): WorkSource {
  return {
    id: 'wsrc_j',
    revision: 1,
    slug: 'jira',
    name: 'jira',
    provider: 'jira',
    locator: null,
    forgeId: null,
    forgeSlug: null,
    forgeName: null,
    credentialId: null,
    credentialSlug: null,
    annotate: false,
    edit: true,
    builtIn: false,
    retired: false,
    createdBy: 'usr_1',
    createdByName: null,
    ...overrides,
  };
}

/** The hub's own built-in work source: always present, with no id, revision, or slug. */
export function builtInWorkSource(overrides: Partial<WorkSource> = {}): WorkSource {
  return workSource({
    id: null,
    revision: null,
    slug: null,
    name: 'hub',
    provider: null,
    builtIn: true,
    edit: false,
    createdBy: null,
    ...overrides,
  });
}
