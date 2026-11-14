import { appKeyPair, chunkProgress, expect, FIXTURE_FORGE, FIXTURE_TOKEN, stepUntil, test, type ForgeApi, type HubApi, type Repository, type RunnerHost, type Wire } from '../../harness/fixtures';

type Clock = Parameters<Parameters<typeof test>[2]>[0]['clock'];

// GitHub App installation credentials, end to end, under the mock forge's `strict_tokens`: only a token the hub minted
// for the installation authenticates, so a pass that goes through proves the hub minted, narrowed, and renewed it. The
// fixture token stays registered as classic throughout, so the fixture's own forge reads keep working. The clock is
// the stack's, so a token's expiry is crossed by advancing it.

const APP = 101;
/** A second App, whose key is replaced on the forge alone. */
const REKEYED_APP = 202;
/** Seconds a minted token lives: shorter than one advance, so every advance crosses an expiry. */
const TOKEN_LIFETIME = 600;
/** One advance of the stack's clock: past a token's expiry and past the forge-status full-pass floor (600s). */
const ADVANCE = 900;
/** Every permission the landing and work-source scopes need or use. */
const FULL = { metadata: 'read', contents: 'write', pull_requests: 'write', issues: 'write', checks: 'read', statuses: 'read', actions: 'write' } as const;

const settled = (s: Wire.ChunkDetail) => ['done', 'needs_human', 'stopped'].includes(s.status);
const fullName = (repo: Repository) => `${repo.owner}/${repo.name}`;

/** A delivery graph whose build commits one file to `repo` and whose `deliver` lands it under the repository's policy. */
function graph(repo: Repository, name: string) {
  return {
    name, entry: 'build', nodes: {
      build: {
        executor: 'runner',
        prompt: [
          'import os, pathlib, subprocess',
          `name = '${repo.name}'`,
          "repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name)",
          "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/app-installation'], check=True)",
          "(pathlib.Path(repo) / 'APP_INSTALLATION.md').write_text('landed with an installation token\\n')",
          "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
          "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a change landed with an installation token'], check=True)",
          "sha = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
          "subprocess.run(['git', '-C', repo, 'push', 'origin', 'feature/app-installation'], check=True)",
          "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feature/app-installation', '--commit', sha], check=True)",
          '',
        ].join('\n'),
        judgement: { prompt: "verdict('pass', 'committed the change')\n", choices: { pass: { description: 'Committed.', to: 'deliver' } } },
        retries: { max: 1, exhausted: 'escalate' },
      },
      deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 600, judgement: { choices: {
        landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
        failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check failed.', to: 'build' },
      } } },
    },
  };
}

/** A `github.app-installation` credential on the fixture forge for `installation` of `app`, reading the secret `secret`. */
async function credential(api: HubApi, slug: string, secret: string, app: number, installation: number) {
  const created = await api.post('/api/credentials', {
    data: { slug, forge: FIXTURE_FORGE, binding: 'github.app-installation', secret, settings: { app_id: app, installation_id: installation } },
  });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
}

async function secret(api: HubApi, name: string, value: string) {
  const created = await api.post('/api/secrets', { data: { name, value } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
}

/** Rebinds `repo`'s repository record to the credential `slug`, and its work source too unless `repositoryOnly`. */
async function bind(api: HubApi, repo: Repository, slug: string, repositoryOnly = false) {
  const repository = await api.patch(`/api/repositories/${repo.name}`, { data: { credential: slug } });
  expect(repository.status, JSON.stringify(repository.body)).toBe(200);
  if (repositoryOnly) return;
  const source = await api.patch(`/api/work-sources/${repo.tokenPrefix}`, { data: { credential: slug, annotate: true } });
  expect(source.status, JSON.stringify(source.body)).toBe(200);
}

/** One turn of the flow driver on a controlled hub, the stack's clock first moved to the pending visit's poll. */
async function flowTurn(api: HubApi, clock: Pick<Clock, 'now' | 'set'>, id: string): Promise<Wire.ChunkDetail> {
  const read = async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body;
  const row = await read();
  const next = row.pending?.next_poll_at ? Date.parse(row.pending.next_poll_at) : NaN;
  if (next > clock.now().getTime()) await clock.set(new Date(next));
  await api.flowStep();
  return read();
}

async function settle(host: RunnerHost, api: HubApi, clock: Clock, id: string): Promise<Wire.ChunkDetail> {
  return stepUntil(host, () => flowTurn(api, clock, id), settled, { stallPasses: 80, progress: chunkProgress });
}

type Event = { kind: string; chunk_id: string | null; message: string; detail?: Record<string, unknown> };
async function events(api: HubApi, kind: string): Promise<Event[]> {
  const feed = (await api.get<{ events: Event[] }>('/api/events?limit=200')).body.events;
  return feed.filter((event) => event.kind === kind);
}

async function labels(forge: ForgeApi, repo: Repository, number: number): Promise<string[]> {
  const answer = await forge.call('GET', `/repos/${fullName(repo)}/issues/${number}`);
  expect(answer.status, JSON.stringify(answer.body)).toBe(200);
  return (answer.body as { labels: { name: string }[] }).labels.map((label) => label.name).sort();
}

test.describe('at fresh-hub', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled' });

  test('test_an_app_installation_lands_and_sweeps_across_expiry_and_restart_and_refuses_each_condition', async ({ api, clock, forge, freshHub, issue, repository, runnerHosts }) => {
    test.slow();
    const first = await repository();
    const second = await repository();
    const checksless = await repository();
    const suspended = await repository();
    const deleted = await repository();
    const rekeyed = await repository();
    const withdrawn = await repository();
    const removed = await repository();
    const rejected = await repository();
    const repos = [first, second, checksless, suspended, deleted, rekeyed, withdrawn, removed, rejected];
    const host = await runnerHosts.create({ runnerRepositories: Object.fromEntries(repos.map((r) => [r.name, r.origin!])) });
    const graphs = new Map<string, string>();
    for (const [index, repo] of repos.entries()) {
      const created = await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph(repo, index === 0 ? 'default-delivery' : `delivery-${repo.name}`)) } });
      expect(created.status, JSON.stringify(created.body)).toBe(201);
      graphs.set(repo.name, created.body.graph_id as string);
    }
    /** Files and ingests an issue in `repo`, puts it on that repository's graph, and promotes it. */
    const promoted = async (repo: Repository, title: string) => {
      const filed = await issue(title, { repository: repo });
      expect((await api.patch(`/api/chunks/${filed.chunk.chunk_id}`, { data: { graph_id: graphs.get(repo.name) } })).status).toBe(202);
      expect((await api.post(`/api/chunks/${filed.chunk.chunk_id}/promote`)).status).toBe(202);
      return { id: filed.chunk.chunk_id, number: filed.issue.number };
    };
    const sweep = () => api.sweep('forge_status', { allTenants: true });
    /** Lands nothing: escalates at `deliver` with `cause`, and its escalation detail. */
    const escalates = async (repo: Repository, title: string, cause: string) => {
      const { id } = await promoted(repo, title);
      const state = await settle(host, api, clock, id);
      expect(state.status, JSON.stringify(state.escalation)).toBe('needs_human');
      expect(state.escalation!.cause).toBe(cause);
      expect(state.current_node_name).toBe('deliver');
      expect(state.landed).toBe(false);
      return { id, detail: state.escalation!.detail || '' };
    };
    /** The one `forge-auth-refused` event of chunk `id`: its refusal, and the remedy in both its message and `detail`. */
    const refusedWith = async (id: string, refusal: string, remedy: string, escalationDetail: string) => {
      const refused = (await events(api, 'forge-auth-refused')).filter((event) => event.chunk_id === id);
      expect(refused).toHaveLength(1);
      expect(refused[0]!.detail?.refusal).toBe(refusal);
      expect(refused[0]!.message).toContain(remedy);
      expect(escalationDetail).toContain(remedy);
    };

    const keys = appKeyPair();
    await forge.registerToken({ token: FIXTURE_TOKEN, token_kind: 'classic', scopes: ['repo'] });
    await forge.registerApp(APP, keys.publicKey);
    await forge.tokenLifetime(TOKEN_LIFETIME);
    await forge.strictTokens();
    await secret(api, 'app-key', keys.privateKey);

    await test.step('one installation lands several repositories and sweeps their sources across expiry and restart', async () => {
      await forge.registerInstallation({ installation_id: 1001, app_id: APP, repositories: [fullName(first), fullName(second)], permissions: FULL });
      await credential(api, 'app-install', 'app-key', APP, 1001);
      await bind(api, first, 'app-install');
      await bind(api, second, 'app-install');

      const a = await promoted(first, 'land with an installation token');
      await sweep();
      expect(await labels(forge, first, a.number)).toEqual(['blizzard:ingested']);
      const landed = await settle(host, api, clock, a.id);
      expect(landed.status, JSON.stringify(landed.escalation)).toBe('done');
      expect(landed.landed).toBe(true);
      // Promoted once the host is idle, so it waits unclaimed until it is settled after the restart.
      const b = await promoted(second, 'wait for a landing across a restart');

      // Past every token's expiry, the sweep reads and writes each source under a renewed token.
      await clock.advance(ADVANCE);
      await sweep();
      expect(await labels(forge, first, a.number)).toEqual([]);
      expect(await labels(forge, second, b.number)).toEqual(['blizzard:ingested']);

      // A restart holds no token, so the next use mints one.
      await freshHub.stop();
      await freshHub.start();
      await clock.advance(ADVANCE);
      const hand = await forge.call('DELETE', `/repos/${fullName(second)}/issues/${b.number}/labels/blizzard:ingested`);
      expect(hand.status, JSON.stringify(hand.body)).toBe(200);
      await sweep();
      expect(await labels(forge, second, b.number)).toEqual(['blizzard:ingested']);
      const after = await settle(host, api, clock, b.id);
      expect(after.status, JSON.stringify(after.escalation)).toBe('done');
      expect(after.landed).toBe(true);
      expect(await events(api, 'forge-auth-refused')).toEqual([]);

      const forges = await api.get<Wire.ForgesListView>('/api/forges');
      const forgeId = forges.body.forges[0]!.forge_id;
      const budgets = (await api.get<Wire.ForgeDetail>(`/api/forges/${forgeId}`)).body.budgets;
      const installation = budgets.filter((entry) => entry.authority === `app/${APP}/installation/1001` && entry.bucket === 'core');
      expect(installation, JSON.stringify(budgets)).toHaveLength(1);
    });

    await test.step('an installation without checks: read escalates forge-capability-missing at the pass start', async () => {
      const { checks: _checks, ...withoutChecks } = FULL;
      await forge.registerInstallation({ installation_id: 1002, app_id: APP, repositories: [fullName(checksless)], permissions: withoutChecks });
      await credential(api, 'app-checksless', 'app-key', APP, 1002);
      await bind(api, checksless, 'app-checksless', true);
      const { id } = await escalates(checksless, 'refuse an installation without checks', 'forge-capability-missing');
      const missing = (await events(api, 'forge-capability-missing')).filter((event) => event.chunk_id === id);
      expect(missing).toHaveLength(1);
      expect(missing[0]!.detail?.credential).toBe('app-checksless');
    });

    await test.step('a suspended installation escalates installation-suspended, and the sweep records it', async () => {
      await forge.registerInstallation({ installation_id: 1003, app_id: APP, repositories: [fullName(suspended)], permissions: FULL });
      await credential(api, 'app-suspended', 'app-key', APP, 1003);
      await bind(api, suspended, 'app-suspended');
      const filed = await issue('refuse a suspended installation', { repository: suspended });
      await forge.suspendInstallation(1003);
      expect((await api.patch(`/api/chunks/${filed.chunk.chunk_id}`, { data: { graph_id: graphs.get(suspended.name) } })).status).toBe(202);
      expect((await api.post(`/api/chunks/${filed.chunk.chunk_id}/promote`)).status).toBe(202);
      const state = await settle(host, api, clock, filed.chunk.chunk_id);
      expect(state.status).toBe('needs_human');
      expect(state.escalation!.cause).toBe('forge-auth');
      await refusedWith(filed.chunk.chunk_id, 'installation-suspended', "Unsuspend the App's installation.", state.escalation!.detail || '');
      await clock.advance(ADVANCE);
      await sweep();
      const source = (await api.get<{ work_source_id: string }>(`/api/work-sources/${suspended.tokenPrefix}`)).body.work_source_id;
      const swept = (await events(api, 'forge-auth-refused')).filter((event) => event.detail?.work_source_id === source);
      expect(swept.map((event) => event.detail?.refusal)).toEqual(['installation-suspended']);
    });

    await test.step('a deleted installation escalates installation-missing, and the sweep records its refused build', async () => {
      await forge.registerInstallation({ installation_id: 1004, app_id: APP, repositories: [fullName(deleted)], permissions: FULL });
      await credential(api, 'app-deleted', 'app-key', APP, 1004);
      await bind(api, deleted, 'app-deleted', true);
      await forge.deleteInstallation(1004);
      const { id, detail } = await escalates(deleted, 'refuse a deleted installation', 'forge-auth');
      await refusedWith(id, 'installation-missing', 'Reinstall the App, or correct the installation id.', detail);
      // Bound only now, the source's binding is built afresh, and the build itself is refused.
      const source = await api.patch(`/api/work-sources/${deleted.tokenPrefix}`, { data: { credential: 'app-deleted', annotate: true } });
      expect(source.status, JSON.stringify(source.body)).toBe(200);
      const sourceId = (source.body as { work_source_id: string }).work_source_id;
      await sweep();
      const swept = (await events(api, 'forge-auth-refused')).filter((event) => event.detail?.work_source_id === sourceId);
      expect(swept.map((event) => event.detail?.refusal)).toEqual(['installation-missing']);
    });

    await test.step('an App key replaced on the forge escalates key-rejected', async () => {
      await forge.registerApp(REKEYED_APP, keys.publicKey);
      await forge.registerInstallation({ installation_id: 2001, app_id: REKEYED_APP, repositories: [fullName(rekeyed)], permissions: FULL });
      await credential(api, 'app-rekeyed', 'app-key', REKEYED_APP, 2001);
      await bind(api, rekeyed, 'app-rekeyed', true);
      await forge.registerApp(REKEYED_APP, appKeyPair().publicKey);
      const { id, detail } = await escalates(rekeyed, 'refuse a replaced App key', 'forge-auth');
      await refusedWith(id, 'key-rejected', "Replace the App's private key, or correct its App id, or check the hub's clock.", detail);
    });

    await test.step('a withdrawn required permission escalates permission-missing naming it', async () => {
      const { contents: _contents, ...withoutContents } = FULL;
      await forge.registerInstallation({ installation_id: 1005, app_id: APP, repositories: [fullName(withdrawn)], permissions: withoutContents });
      await credential(api, 'app-withdrawn', 'app-key', APP, 1005);
      await bind(api, withdrawn, 'app-withdrawn', true);
      const { id, detail } = await escalates(withdrawn, 'refuse a withdrawn permission', 'forge-auth');
      await refusedWith(id, 'permission-missing', 'Grant contents=write to the token or the App installation.', detail);
    });

    await test.step('a repository removed from the installation escalates as one the forge does not know', async () => {
      await forge.registerInstallation({ installation_id: 1006, app_id: APP, repositories: [fullName(removed)], permissions: FULL });
      await credential(api, 'app-removed', 'app-key', APP, 1006);
      await bind(api, removed, 'app-removed', true);
      await forge.removeRepository(1006, fullName(removed));
      const { detail } = await escalates(removed, 'refuse a removed repository', 'forge-config');
      expect(detail).toContain('or the installation does not include it');
    });

    await test.step('a token refused again after its re-mint escalates credential-invalid', async () => {
      await forge.registerInstallation({ installation_id: 1007, app_id: APP, repositories: [fullName(rejected)], permissions: FULL });
      await credential(api, 'app-rejected', 'app-key', APP, 1007);
      await bind(api, rejected, 'app-rejected', true);
      // Held at `deliver` by a pending check, so the refusal meets the landing's own requests and not the fixture's.
      const repo = { repo: fullName(rejected) };
      expect((await forge.call('POST', '/_levers/checks_pending', repo)).status).toBe(200);
      const { id } = await promoted(rejected, 'refuse a token the hub cannot renew');
      await stepUntil(host, () => flowTurn(api, clock, id), (s) => s.pending !== null || settled(s), { stallPasses: 80, progress: chunkProgress });
      expect((await forge.call('POST', '/_levers/token_rejected', repo)).status).toBe(200);
      try {
        expect((await forge.call('DELETE', `/_levers/checks_pending?repo=${encodeURIComponent(repo.repo)}`)).status).toBe(200);
        const state = await settle(host, api, clock, id);
        expect(state.status).toBe('needs_human');
        expect(state.escalation!.cause).toBe('forge-auth');
        await refusedWith(id, 'credential-invalid', "Replace the token, or check the App's installation.", state.escalation!.detail || '');
      } finally {
        expect((await forge.call('DELETE', `/_levers/token_rejected?repo=${encodeURIComponent(repo.repo)}`)).status).toBe(200);
      }
    });
  });
});
