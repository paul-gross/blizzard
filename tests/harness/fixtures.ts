import * as fs from 'node:fs';
import { expect } from '@playwright/test';
import { type CliResult, CliTimeout, runCommand } from './cli';
import { fleetApi, forgeApi, type ForgeApi, type HubApi, hubApi, mockRunnerApi, type Verbs } from './client/clients';
import type { RunnerAddRequest } from './client/wire/hub/types.gen';
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
  project: Project;
  fleet: (name: string, options?: RunnerOptions) => Promise<FleetClient>;
  forge: ForgeApi;
  events: Events;
  hubLog: HubLog;
  /** Runs the real `blizzard` CLI against the test's hub and tenant, with a config home of its own. */
  cli: (args: string[]) => Promise<CliResult>;
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

  events: async ({ tenant, testHub, level, ledger, openedSubscriptions }, use) => {
    await use(
      eventStream({
        hubUrl: testHub.coordinates.url,
        tenantId: tenant.id,
        ledger,
        narrow: sharesTenant(level.served),
        opened: openedSubscriptions,
      }),
    );
  },

  hubLog: async ({ tenant, level, testHub }, use) => {
    await use(readHubLog({ logPath: testHub.coordinates.logPath, tenantId: tenant.id, fresh: level.served === 'fresh-hub' }));
  },

  cli: async ({ testHub, tenant }, use, testInfo) => {
    await use(async (args) => {
      const home = testInfo.outputPath('cli-config-home');
      fs.mkdirSync(home, { recursive: true });
      const env = daemonEnv({ BZ_HUB_URL: testHub.coordinates.url, BZ_HUB_TENANT: tenant.id }, home);
      try {
        return await runCommand(requireBinary(binDir(), 'blizzard'), args, { env });
      } catch (error) {
        if (error instanceof CliTimeout) {
          await testInfo.attach('cli stdout', { body: error.stdout, contentType: 'text/plain' });
          await testInfo.attach('cli stderr', { body: error.stderr, contentType: 'text/plain' });
        }
        throw error;
      }
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

export { expect };
