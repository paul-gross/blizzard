import * as fs from 'node:fs';
import { expect } from '@playwright/test';
import { type People, type Person, peopleFactory } from './people';
import { walk } from './auth';
import { type CliResult, CliTimeout, type Prompt, runCommand } from './cli';
import { fleetApi, forgeApi, type ForgeApi, type HubApi, hubApi, mockRunnerApi, type Verbs } from './client/clients';
import type { Role, RunnerAddRequest } from './client/wire/hub/types.gen';
import { type Events, eventStream, type Subscription } from './events';
import {
  type FactoryContext,
  type FiledIssue,
  issueFactory,
  type MockRunner,
  mockRunnerFactory,
  type Repository,
  repositoryFactory,
  requireOwnTenant,
  type RunnerOptions,
} from './factories';
import { binDir, daemonEnv, requireBinary } from './env';
import { type HubLog, hubLog as readHubLog } from './logs';
import { type OtlpSink, startOtlpSink } from './otlp-sink';
import { type Project, sharesTenant, tenancy, uniqueName } from './tenancy';

/** A fleet-plane client for one runner, under the token minted for it. */
export interface FleetClient extends Verbs {
  runnerId: string;
  token: string;
}

interface Fixtures {
  api: HubApi;
  /** The role `user` is invited as; `test.use({ userRole })`. */
  userRole: Role;
  /** One signed-in person in `tenant` with the role `userRole` names. Needs a hub with authentication on. */
  user: Person;
  /** Signs people in through the stub IdP: `create` for further invited people, `signIn` for an identity the test names. */
  people: People;
  project: Project;
  fleet: (name: string, options?: RunnerOptions) => Promise<FleetClient>;
  forge: ForgeApi;
  events: Events;
  hubLog: HubLog;
  /**
   * Runs the real `blizzard` CLI against the test's hub and tenant, with a config home of its own. With `as`, the CLI
   * acts as that person: the first call for a person logs them in (`hub login --paste`) in a config home of theirs.
   */
  cli: (args: string[], options?: { as?: Person }) => Promise<CliResult>;
  /** Starts a loopback OTLP/HTTP receiver, closed when the test ends. */
  otlpSink: () => Promise<OtlpSink>;
  issue: (title: string, options?: { repository?: Repository }) => Promise<FiledIssue>;
  repository: (name?: string) => Promise<Repository>;
  mockRunner: (name: string, options?: RunnerOptions) => Promise<MockRunner>;
  /** Internal: what the factories share. */
  factoryContext: FactoryContext;
  /** Internal: the event subscriptions a test opened, closed when it ends. */
  openedSubscriptions: Subscription[];
}

/** The package's `test` and `expect`: the only module a spec imports. */
export const test = tenancy.extend<Fixtures>({
  api: async ({ testHub, tenant, level, ledger }, use, testInfo) => {
    await use(
      hubApi({
        context: testHub.context,
        testInfo,
        tenantId: tenant.id,
        narrow: sharesTenant(level.served),
        level: level.served,
        ledger,
        logContext: () => testHub.logTail(tenant.id),
      }),
    );
  },

  userRole: ['contributor', { option: true }],

  people: async ({ testHub, tenantOf, ledger, level, playwright }, use, testInfo) => {
    const { people, disposeAll } = peopleFactory({
      testHub,
      tenantOf,
      ledger,
      testInfo,
      level: level.served,
      newContext: () => playwright.request.newContext({ baseURL: testHub.coordinates.url }),
      unique: (prefix) => uniqueName(testInfo, prefix),
    });
    await use(people);
    await disposeAll();
  },

  user: async ({ people, userRole }, use) => {
    await use(await people.create({ role: userRole }));
  },

  project: async ({ level, tenant, api }, use, testInfo) => {
    if (level.served === 'fresh-project') {
      const slug = uniqueName(testInfo, 'p-');
      const created = await api.post('/api/projects', { data: { slug, name: slug } });
      if (!created.ok) throw new Error(`creating project ${slug} answered ${created.status}: ${JSON.stringify(created.body)}`);
      await use({ id: created.body.project_id as string, slug });
      return;
    }
    // The tenant's `default` project; `unfiltered`, because the ledger holds no project the test did not create.
    const listed = await api.unfiltered.get('/api/projects');
    const found = (listed.body.projects as { project_id: string; slug: string }[]).find((candidate) => candidate.slug === 'default');
    if (found === undefined) throw new Error(`tenant ${tenant.id} has no default project`);
    await use({ id: found.project_id, slug: found.slug });
  },

  fleet: async ({ api, testHub, tenant, level, ledger }, use, testInfo) => {
    await use(async (name, options) => {
      requireOwnTenant(level.served, 'fleet()');
      const minted = await api.post('/api/runners', { data: { name, ...options } satisfies RunnerAddRequest });
      if (!minted.ok) throw new Error(`minting runner ${name} answered ${minted.status}: ${JSON.stringify(minted.body)}`);
      ledger.add('runner', minted.body.runner_id as string);
      const client = fleetApi({
        context: testHub.context,
        testInfo,
        token: minted.body.token as string,
        ledger,
        logContext: () => testHub.logTail(tenant.id),
      });
      return { ...client, runnerId: minted.body.runner_id as string, token: minted.body.token as string };
    });
  },

  forge: async ({ testHub, ledger }, use, testInfo) => {
    await use(forgeApi({ context: testHub.context, testInfo, url: testHub.coordinates.forgeUrl, ledger }));
  },

  openedSubscriptions: async ({}, use) => {
    const opened: Subscription[] = [];
    await use(opened);
    for (const subscription of opened) subscription.close();
  },

  events: async ({ tenant, testHub, level, ledger, openedSubscriptions, api, issue }, use) => {
    await use(
      eventStream({
        hubUrl: testHub.coordinates.url,
        tenantId: tenant.id,
        ledger,
        narrow: sharesTenant(level.served),
        fromNow: sharesTenant(level.served),
        opened: openedSubscriptions,
        superuserSession: async () => (await testHub.superuser())?.session,
        barrier: {
          file: async () => (await issue('barrier')).chunk.chunk_id,
          edit: async (chunkId, effort) => {
            const edited = await api.patch(`/api/chunks/${chunkId}`, { data: { default_effort: effort } });
            if (edited.status !== 202) throw new Error(`editing barrier chunk ${chunkId} answered ${edited.status}: ${JSON.stringify(edited.body)}`);
          },
        },
      }),
    );
  },

  hubLog: async ({ tenant, level, testHub }, use) => {
    await use(readHubLog({ logPath: testHub.coordinates.logPath, tenantId: tenant.id, fresh: level.served === 'fresh-hub' }));
  },

  cli: async ({ testHub, tenant }, use, testInfo) => {
    const run = async (args: string[], env: NodeJS.ProcessEnv, prompt?: Prompt): Promise<CliResult> => {
      try {
        return await runCommand(requireBinary(binDir(), 'blizzard'), args, { env, prompt });
      } catch (error) {
        if (error instanceof CliTimeout) {
          await testInfo.attach('cli stdout', { body: error.stdout, contentType: 'text/plain' });
          await testInfo.attach('cli stderr', { body: error.stderr, contentType: 'text/plain' });
        }
        throw error;
      }
    };
    const envFor = (home: string): NodeJS.ProcessEnv => {
      fs.mkdirSync(home, { recursive: true });
      // Unbuffered, so the login URL reaches the harness before the command waits on the code.
      return daemonEnv({ BZ_HUB_URL: testHub.coordinates.url, BZ_HUB_TENANT: tenant.id, PYTHONUNBUFFERED: '1' }, home);
    };
    // One login per person per test, in the config home that person's later commands read.
    const logins = new Map<string, Promise<void>>();
    const login = (person: Person, env: NodeJS.ProcessEnv): Promise<void> => {
      let done = logins.get(person.userId);
      if (done === undefined) {
        done = (async () => {
          const result = await run(['hub', 'login', '--paste'], env, {
            until: /open this URL to log in, then paste the code it shows: (\S+)/,
            reply: async (match) => {
              const landing = await walk({ context: person.context, hub: testHub.coordinates, email: person.email, startUrl: match[1] as string });
              const code = /<pre[^>]*>([^<]+)<\/pre>/.exec(landing.body)?.[1];
              if (landing.status !== 200 || code === undefined) {
                throw new Error(`the login URL for ${person.email} landed at ${landing.status} ${landing.location ?? ''} with no code: ${landing.body.slice(0, 300)}`);
              }
              return code.trim();
            },
          });
          if (result.code !== 0) throw new Error(`\`blizzard hub login --paste\` for ${person.email} exited ${result.code}: ${result.stdout}${result.stderr}`);
        })();
        logins.set(person.userId, done);
      }
      return done;
    };
    await use(async (args, options = {}) => {
      const person = options.as;
      const env = envFor(testInfo.outputPath(person === undefined ? 'cli-config-home' : `cli-config-home-${person.username}`));
      if (person !== undefined) await login(person, env);
      return run(args, env);
    });
  },

  otlpSink: async ({}, use) => {
    const started: OtlpSink[] = [];
    await use(async () => {
      const sink = await startOtlpSink();
      started.push(sink);
      return sink;
    });
    await Promise.all(started.map((sink) => sink.close()));
  },

  factoryContext: async ({ api, forge, project, level, testHub, ledger }, use, testInfo) => {
    await use({ api, forge, project, level: level.served, testHub, ledger, unique: (prefix) => uniqueName(testInfo, prefix),
      mockClient: (url) => mockRunnerApi({ context: testHub.context, testInfo, url, ledger }) });
  },

  repository: async ({ factoryContext }, use) => {
    await use(repositoryFactory(factoryContext));
  },

  issue: async ({ factoryContext, repository }, use) => {
    await use(issueFactory(factoryContext, repository));
  },

  mockRunner: async ({ factoryContext }, use) => {
    const { factory, stopAll } = mockRunnerFactory(factoryContext);
    await use(factory);
    await stopAll();
  },
});

export type { TranscriptSegmentRecord } from './client/wire/hub/types.gen';
export { expect };
