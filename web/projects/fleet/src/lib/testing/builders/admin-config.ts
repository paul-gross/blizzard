import type {
  ConfigChangesPage,
  ConfigChangeView,
  ForgeDetail,
  MemberView,
  ProjectView,
  RepositoryLinkView,
  RepositorySummary,
  SecretView,
  SourceLinkView,
  WorkSourceSummary,
} from '../../api/hub';
import { fixtureInstant } from './clock';

/** A project record at its first revision. */
export function projectView(overrides: Partial<ProjectView> = {}): ProjectView {
  return {
    project_id: 'proj_b',
    slug: 'blizzard',
    name: 'Blizzard',
    description: '',
    color: 'blue',
    created_at: fixtureInstant(-30 * 24 * 3600),
    revision: 1,
    ...overrides,
  };
}

/** A project's link to a work source — an operator-added, linked source at its first revision. */
export function sourceLinkView(overrides: Partial<SourceLinkView> = {}): SourceLinkView {
  return {
    project_id: 'proj_b',
    name: 'jira',
    slug: 'jira',
    linked: true,
    built_in: false,
    revision: 1,
    ...overrides,
  };
}

/** A project's link to a repository — linked, unranked in the landing order, at its first revision. */
export function repositoryLinkView(overrides: Partial<RepositoryLinkView> = {}): RepositoryLinkView {
  return {
    project_id: 'proj_b',
    repository_id: 'repo_c',
    name: 'core',
    slug: 'core',
    linked: true,
    position: null,
    revision: 1,
    ...overrides,
  };
}

/** An operator-added, live work source at its first revision. */
export function workSourceSummary(overrides: Partial<WorkSourceSummary> = {}): WorkSourceSummary {
  return {
    work_source_id: 'wsrc_j',
    name: 'jira',
    slug: 'jira',
    provider: 'jira',
    locator: null,
    api_base: null,
    web_base: null,
    forge_id: null,
    forge_name: null,
    forge_slug: null,
    secret: null,
    annotate: false,
    edit: true,
    built_in: false,
    retired: false,
    revision: 1,
    created_at: fixtureInstant(-30 * 24 * 3600),
    created_by: 'usr_1',
    created_by_name: null,
    warnings: [],
    ...overrides,
  };
}

/** A live repository on a GitHub forge at its first revision. */
export function repositorySummary(overrides: Partial<RepositorySummary> = {}): RepositorySummary {
  return {
    repository_id: 'repo_c',
    name: 'core',
    slug: 'core',
    owner: 'acme',
    repo: 'core',
    base_branch: 'main',
    forge_id: 'frg_g',
    forge_name: 'GitHub',
    forge_slug: 'github',
    forge_api_url: 'https://api.github.com',
    landing_policy: 'merge',
    park_timeout_hours: null,
    secret_name: 'gh-token',
    retired: false,
    revision: 1,
    created_at: fixtureInstant(-30 * 24 * 3600),
    created_by: 'usr_1',
    created_by_name: null,
    warnings: [],
    ...overrides,
  };
}

/** A live, unreferenced secret at its first revision. */
export function secretView(overrides: Partial<SecretView> = {}): SecretView {
  return {
    name: 'gh-token',
    references: [],
    retirable: true,
    retired: false,
    revision: 1,
    created_at: fixtureInstant(-30 * 24 * 3600),
    replaced_at: fixtureInstant(-30 * 24 * 3600),
    replaced_by: 'usr_1',
    replaced_by_name: null,
    ...overrides,
  };
}

/** A tenant member: a contributor with no linked identities the viewer can neither re-role nor revoke. */
export function memberView(overrides: Partial<MemberView> = {}): MemberView {
  return {
    user_id: 'usr_2',
    username: 'grace',
    display_name: 'Grace',
    email: null,
    role: 'contributor',
    hub_admin: false,
    identities: [],
    assignable_roles: [],
    revocable: false,
    created_at: fixtureInstant(-30 * 24 * 3600),
    ...overrides,
  };
}

/** A live GitHub forge with nothing referencing it, at its first revision. */
export function forgeDetail(overrides: Partial<ForgeDetail> = {}): ForgeDetail {
  return {
    forge_id: 'frg_g',
    name: 'GitHub',
    slug: 'github',
    kind: 'github',
    api_base: 'https://api.github.com',
    web_base: null,
    web_origin: 'https://github.com',
    budgets: [],
    offered: [],
    repositories: [],
    work_sources: [],
    retired: false,
    revision: 1,
    created_at: fixtureInstant(-30 * 24 * 3600),
    created_by: 'usr_1',
    created_by_name: null,
    ...overrides,
  };
}

/** One config-change entry: an operator's board edit of a project at revision 2. */
export function configChangeView(overrides: Partial<ConfigChangeView> = {}): ConfigChangeView {
  return {
    id: 1,
    actor: 'usr_1',
    actor_name: null,
    apply_id: null,
    diff: [],
    door: 'board',
    op: 'update',
    project_id: null,
    record_key: 'proj_b',
    record_kind: 'project',
    record_label: null,
    recorded_at: fixtureInstant(-3600),
    revision: 2,
    ...overrides,
  };
}

/** One page of the config-change history — the last page, `total` counting its entries. */
export function configChangesPage(overrides: Partial<ConfigChangesPage> = {}): ConfigChangesPage {
  const changes = overrides.changes ?? [];
  return { changes, next_before: null, total: changes.length, ...overrides };
}
