import * as fs from 'node:fs';
import * as path from 'node:path';
import type { ChunkIngestResponse, RunnerAddRequest } from './client/wire/hub/types.gen';
import { type ForgeApi, type HubApi } from './client/clients';
import type { ApiResponse } from './client/engine';
import type { Ledger } from './client/ledger';
import type { MockDriveAnswer, MockIssue, MockRunnerLeverListing } from './client/mock';
import { startMockRunner } from './daemons/mock-runner';
import { type DaemonRecord, stopDaemon } from './daemons/process';
import { runDir } from './env';
import { COMMON_OWNER, COMMON_REPOSITORY, COMMON_SOURCE, FIXTURE_SECRET } from './hubs';
import { scanLog } from './logs';
import { type Level, type Project, sharesTenant, type TestHub } from './tenancy';

/** A repository on the hub's forge, and the work-source token prefix an ingest names it by: `<tokenPrefix>:<number>`. */
export interface Repository {
  name: string;
  owner: string;
  tokenPrefix: string;
  /** The forge's bare Git repository, also used by the runner's basic workspace. */
  origin?: string;
}

export interface MockRunner {
  name: string;
  runnerId: string;
  token: string;
  url: string;
  drive<V extends string>(verb: V, body?: unknown): Promise<ApiResponse<MockDriveAnswer<V>>>;
  lever(kind: string, body?: unknown): Promise<ApiResponse>;
  levers(): Promise<ApiResponse<MockRunnerLeverListing>>;
}

export type RunnerOptions = Pick<RunnerAddRequest, 'projects'>;

export interface FiledIssue {
  issue: MockIssue;
  chunk: ChunkIngestResponse;
}

export interface FactoryContext {
  api: HubApi;
  forge: ForgeApi;
  project: Project;
  level: Level;
  testHub: TestHub;
  ledger: Ledger;
  mockClient: (url: string) => Pick<MockRunner, 'drive' | 'lever' | 'levers'>;
  /** A name unique to this test and run. */
  unique: (prefix: string) => string;
}

/** The common tenant's repository, where every test below `fresh-tenant` files its issues. */
const COMMON: Repository = { name: COMMON_REPOSITORY, owner: COMMON_OWNER, tokenPrefix: COMMON_SOURCE };

/** `fleet`, `repository` and `mockRunner` refuse below `fresh-tenant`: a runner, a repository, or a work source is tenant-wide. */
export function requireOwnTenant(level: Level, what: string): void {
  if (sharesTenant(level)) {
    throw new Error(
      `${what} is tenant-wide, and this test is served "${level}", where the tenant is shared. Declare isolation "fresh-tenant" or above.`,
    );
  }
}

/** An all-tenant act refuses below `fresh-hub`: on a shared hub it would reach every other test's tenant. */
export function requireFreshHub(level: Level, what: string): void {
  if (level !== 'fresh-hub') {
    throw new Error(`${what} reaches every tenant of the hub, and this test is served "${level}". Declare isolation "fresh-hub".`);
  }
}

/**
 * At `fresh-tenant` and up, a repository on the hub's forge named from the test's id unless a name is given, then one
 * `POST /api/config/apply` declaring it as a work source of the tenant, the repository, and their links to `project`.
 */
export function repositoryFactory(context: FactoryContext): (name?: string) => Promise<Repository> {
  const made = new Map<string, Promise<Repository>>();
  // The project's links are declared whole by each apply, so a later repository carries the earlier ones with it.
  const linked: string[] = [];
  // A name asked for twice is the one repository: a host's configuration may have made it first.
  return (name) => {
    if (name === undefined) return declare(context, context.unique('r-'), linked);
    let found = made.get(name);
    if (found === undefined) {
      found = declare(context, name, linked);
      made.set(name, found);
    }
    return found;
  };
}

async function declare(context: FactoryContext, repository: string, linked: string[]): Promise<Repository> {
  requireOwnTenant(context.level, 'repository()');
  linked.push(repository);
  const created = await context.forge.createRepository(COMMON_OWNER, repository);
  // The work source and the repository each name a credential; a second call finds the secret already there.
  const secret = await context.api.post('/api/secrets', { data: { name: FIXTURE_SECRET, value: 'fixture-token' } });
  if (secret.status !== 201 && secret.status !== 409) {
    throw new Error(`creating the fixture secret answered ${secret.status}: ${JSON.stringify(secret.body)}`);
  }
  const applied = await context.api.post('/api/config/apply', {
    data: {
      version: 1,
      secrets: [FIXTURE_SECRET],
      work_sources: [
        {
          slug: repository,
          provider: 'github',
          locator: `${COMMON_OWNER}/${repository}`,
          api_base: context.forge.url,
          secret: FIXTURE_SECRET,
        },
      ],
      repositories: [
        {
          slug: repository,
          forge_api_url: context.forge.url,
          owner: COMMON_OWNER,
          repo: repository,
          base_branch: 'main',
          secret_name: FIXTURE_SECRET,
        },
      ],
      projects: [{ slug: context.project.slug, repositories: [...linked], sources: [...linked] }],
    },
  });
  if (!applied.ok) throw new Error(`declaring ${repository} answered ${applied.status}: ${JSON.stringify(applied.body)}`);
  return { name: repository, owner: COMMON_OWNER, tokenPrefix: repository, origin: created.origin };
}

/**
 * Files an issue on the hub's forge and ingests it with `POST /api/chunks`, both recorded as the test's own. Below
 * `fresh-tenant` it files in the common tenant's shared repository; from `fresh-tenant` up, in the repository it names,
 * or in one `repository()` makes for it.
 */
export function issueFactory(
  context: FactoryContext,
  repository: (name?: string) => Promise<Repository>,
): (title: string, options?: { repository?: Repository }) => Promise<FiledIssue> {
  let own: Repository | undefined;
  let linked = false;
  return async (title, options = {}) => {
    // The project made for the test links nothing yet; the shared source and repository are linked to it, once.
    if (context.level === 'fresh-project' && !linked) {
      for (const [kind, name] of [['source-links', COMMON_SOURCE], ['repository-links', COMMON_REPOSITORY]] as const) {
        const response = await context.api.put(`/api/projects/${context.project.id}/${kind}/${name}`);
        if (!response.ok) throw new Error(`linking ${name} to the test's project answered ${response.status}: ${JSON.stringify(response.body)}`);
      }
      linked = true;
    }
    let target = options.repository;
    if (target === undefined) {
      if (sharesTenant(context.level)) target = COMMON;
      else target = own ??= await repository();
    }
    const issue = await context.forge.createIssue(`${target.owner}/${target.name}`, { title });
    const ingested = await context.api.post<ChunkIngestResponse>('/api/chunks', {
      data: { tokens: [`${target.tokenPrefix}:${issue.number}`], project: context.project.slug },
    });
    if (!ingested.ok) {
      throw new Error(`ingesting ${target.tokenPrefix}:${issue.number} answered ${ingested.status}: ${JSON.stringify(ingested.body)}`);
    }
    return { issue, chunk: ingested.body };
  };
}

/**
 * At `fresh-tenant` and up, a mock runner against the test's hub with a token minted in the tenant, stopped when the
 * test ends. A mock runner left to add itself would name no tenant and be refused, so none starts without `--token`.
 */
export function mockRunnerFactory(context: FactoryContext): { factory: (name: string, options?: RunnerOptions) => Promise<MockRunner>; stopAll: () => Promise<void> } {
  const started: DaemonRecord[] = [];
  return {
    factory: async (name, options) => {
      requireOwnTenant(context.level, 'mockRunner()');
      const minted = await context.api.post('/api/runners', { data: { name, ...options } satisfies RunnerAddRequest });
      if (!minted.ok) throw new Error(`minting runner ${name} answered ${minted.status}: ${JSON.stringify(minted.body)}`);
      context.ledger.add('runner', minted.body.runner_id as string);
      const dir = path.join(runDir(), 'runners', context.unique(`${name}-`));
      fs.mkdirSync(dir, { recursive: true });
      const daemon = await startMockRunner(dir, { hubUrl: context.testHub.coordinates.url, name, token: minted.body.token as string });
      started.push(daemon);
      return { name, runnerId: minted.body.runner_id as string, token: minted.body.token as string, url: daemon.url, ...context.mockClient(daemon.url) };
    },
    // A runner's log is read for the guard's refusal when it stops.
    stopAll: async () => {
      await Promise.all(started.map(stopDaemon));
      const refusals = started.flatMap((daemon) => scanLog(daemon.logPath).map((line) => line.raw));
      if (refusals.length > 0) throw new Error(`a statement guard refused in a mock runner:\n${refusals.join('\n')}`);
    },
  };
}
