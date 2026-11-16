import { appKeyPair, chunkProgress, expect, FIXTURE_FORGE, FIXTURE_TOKEN, stepUntil, test, type ForgeApi, type HubApi, type Repository, type RunnerHost, type Wire } from '../../harness/fixtures';

type Clock = Parameters<Parameters<typeof test>[2]>[0]['clock'];

// Recovering a forge-auth escalation, end to end, under the mock forge's `strict_tokens`: for each refusal the hub
// names, the operator fixes the credential, checks it, and requeues the chunk, and the same change lands with each
// repository's landing recorded once. A permission withdrawn mid-delivery escalates with the change still open, its
// branch still on the forge, and the repositories that already landed kept. The clock is the stack's, moved only to a
// pending visit's own poll.

const APP = 401;
/** A second App, whose key is replaced on the forge alone. */
const REKEYED_APP = 402;
/** Every permission the landing and work-source scopes need or use. */
const FULL = { metadata: 'read', contents: 'write', pull_requests: 'write', issues: 'write', checks: 'read', statuses: 'read', actions: 'write' } as const;
const BRANCH = 'feature/forge-auth-recovery';

const settled = (s: Wire.ChunkDetail) => ['done', 'needs_human', 'stopped'].includes(s.status);
const fullName = (repo: Repository) => `${repo.owner}/${repo.name}`;

/** A delivery graph whose build commits one file to each of `repos` and whose `deliver` lands them in order. */
function graph(repos: Repository[], name: string) {
  const prompt = [
    'import os, pathlib, subprocess',
    `for name in ${JSON.stringify(repos.map((repo) => repo.name))}:`,
    "    path = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name",
    `    subprocess.run(['git', '-C', str(path), 'switch', '-C', '${BRANCH}'], check=True)`,
    "    (path / 'RECOVERY.md').write_text('landed after a recovery\\n')",
    "    subprocess.run(['git', '-C', str(path), 'add', '-A'], check=True)",
    "    subprocess.run(['git', '-C', str(path), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a change landed after a recovery'], check=True)",
    "    sha = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    `    subprocess.run(['git', '-C', str(path), 'push', 'origin', '${BRANCH}'], check=True)`,
    `    subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', '${BRANCH}', '--commit', sha], check=True)`,
    '',
  ].join('\n');
  return JSON.stringify({ name, entry: 'build', nodes: {
    build: { executor: 'runner', prompt, judgement: { prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 600, judgement: { choices: {
      landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
      failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check failed.', to: 'build' },
    } } },
  } });
}

async function secret(api: HubApi, name: string, value: string) {
  const created = await api.post('/api/secrets', { data: { name, value } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
}

async function credential(api: HubApi, slug: string, binding: string, secretName: string, settings: Record<string, unknown> = {}) {
  const created = await api.post('/api/credentials', { data: { slug, forge: FIXTURE_FORGE, binding, secret: secretName, settings } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
}

/** An installation of `app` reaching `repo` with every permission, and the credential `slug` acting through it for `repo`. */
async function installed(api: HubApi, forge: ForgeApi, repo: Repository, slug: string, app: number, installation: number) {
  await forge.registerInstallation({ installation_id: installation, app_id: app, repositories: [fullName(repo)], permissions: FULL });
  await credential(api, slug, 'github.app-installation', 'recovery-app-key', { app_id: app, installation_id: installation });
  await bind(api, repo, slug);
}

/** A `github.token` credential `slug` reading a new secret holding `token`, acting for `repo`. */
async function tokened(api: HubApi, repo: Repository, slug: string, token: string) {
  await secret(api, `${slug}-secret`, token);
  await credential(api, slug, 'github.token', `${slug}-secret`);
  await bind(api, repo, slug);
}

async function bind(api: HubApi, repo: Repository, slug: string) {
  const repository = await api.patch(`/api/repositories/${repo.name}`, { data: { credential: slug } });
  expect(repository.status, JSON.stringify(repository.body)).toBe(200);
}

type Pull = { number: number; state: string; merged: boolean; merge_commit_sha: string | null };
async function pulls(forge: ForgeApi, repo: Repository): Promise<Pull[]> {
  const answer = await forge.call('GET', `/repos/${fullName(repo)}/pulls?state=all`);
  expect(answer.status, JSON.stringify(answer.body)).toBe(200);
  return answer.body as Pull[];
}

type LandingFact = { repo: string; commit_hash: string };
async function landingFacts(api: HubApi, id: string): Promise<LandingFact[]> {
  const facts: LandingFact[] = [];
  let cursor: number | null = 0;
  while (cursor !== null) {
    const answer: { status: number; body: { items: LandingFact[]; next_cursor: number | null } } = await api.get(`/api/chunks/${id}/landing-facts?cursor=${cursor}`);
    expect(answer.status).toBe(200);
    facts.push(...answer.body.items);
    cursor = answer.body.next_cursor;
  }
  return facts;
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

const until = (host: RunnerHost, api: HubApi, clock: Clock, id: string, reached: (s: Wire.ChunkDetail) => boolean) =>
  stepUntil(host, () => flowTurn(api, clock, id), reached, { stallPasses: 80, progress: chunkProgress });

test.describe('at fresh-hub', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled' });

  test('test_each_forge_auth_refusal_recovers_by_fix_check_and_requeue', async ({ api, clock, forge, issue, project, repository, runnerHosts }) => {
    test.slow();
    const rejected = await repository();
    const library = await repository();
    const consumer = await repository();
    const fineGrained = await repository();
    const sso = await repository();
    const rekeyed = await repository();
    const deleted = await repository();
    const suspended = await repository();
    const repos = [rejected, library, consumer, fineGrained, sso, rekeyed, deleted, suspended];
    const host = await runnerHosts.create({ runnerRepositories: Object.fromEntries(repos.map((r) => [r.name, r.origin!])) });
    const graphs = new Map<string, string>();
    /** Puts a chunk for `title`, filed in `targets[0]`, on a graph building every one of `targets`, and promotes it. */
    const promoted = async (targets: Repository[], title: string) => {
      const name = graphs.size === 0 ? 'default-delivery' : `recovery-${targets[0]!.name}`;
      const created = await api.post('/api/graphs', { data: { definition_yaml: graph(targets, name) } });
      expect(created.status, JSON.stringify(created.body)).toBe(201);
      graphs.set(name, created.body.graph_id as string);
      const { chunk } = await issue(title, { repository: targets[0]! });
      expect((await api.patch(`/api/chunks/${chunk.chunk_id}`, { data: { graph_id: created.body.graph_id } })).status).toBe(202);
      expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
      return chunk.chunk_id;
    };
    /** Holds `repo`'s landing at `deliver` behind a pending check, with its change open, until `release` runs. */
    const held = async (repo: Repository) => {
      expect((await forge.call('POST', '/_levers/checks_pending', { repo: fullName(repo) })).status).toBe(200);
      return async () => expect((await forge.call('DELETE', `/_levers/checks_pending?repo=${encodeURIComponent(fullName(repo))}`)).status).toBe(200);
    };
    /** The chunk escalated `forge-auth` with `refusal` at `deliver` for `repo`, its detail ending in the recovery commands. */
    const escalated = async (id: string, repo: Repository, slug: string, refusal: string) => {
      const state = await until(host, api, clock, id, settled);
      expect(state.status, JSON.stringify(state.escalation)).toBe('needs_human');
      expect(state.escalation!.cause).toBe('forge-auth');
      expect(state.current_node_name).toBe('deliver');
      const refused = (await api.get<{ events: { kind: string; detail?: Record<string, unknown> }[] }>(`/api/events?chunk_id=${id}&limit=200`)).body.events
        .filter((event) => event.kind === 'forge-auth-refused');
      expect(refused.map((event) => event.detail?.refusal)).toEqual([refusal]);
      const detail = state.escalation!.detail || '';
      expect(detail.trimEnd().split('\n').at(-1)).toBe(`Then run: blizzard hub credential check ${slug} --repo ${repo.name}; blizzard hub chunk requeue ${id}.`);
      return state;
    };
    /** Checks `slug` for `repo`, finding nothing refused or unsupported, then requeues chunk `id` and settles it landed. */
    const recovered = async (id: string, slug: string, landed: Repository[], open: Map<string, number>) => {
      const repo = landed.at(-1)!;
      const checked = await api.post<Wire.CredentialCheckView>(`/api/credentials/${slug}/checks`, { data: { repositories: [repo.name] }, timeout: 90_000 });
      expect(checked.status, JSON.stringify(checked.body)).toBe(200);
      const outcomes = [...(checked.body.mint ? [checked.body.mint] : []), ...checked.body.uses.flatMap((use) => use.operations)];
      expect(checked.body.uses.map((use) => [use.slug, use.reached])).toEqual([[repo.name, true]]);
      expect(outcomes.filter((operation) => ['refused', 'unsupported'].includes(operation.outcome as string)), JSON.stringify(outcomes)).toEqual([]);
      // The person requeues at an instant of their own, not the escalation's.
      await clock.advance(1);
      expect((await api.post(`/api/chunks/${id}/requeues`)).status).toBe(202);
      const done = await until(host, api, clock, id, settled);
      expect(done.status, JSON.stringify(done.escalation)).toBe('done');
      expect(done.landed).toBe(true);
      const facts = await landingFacts(api, id);
      expect(facts.map((fact) => fact.repo)).toEqual(landed.map(fullName));
      expect(done.landed_repos.map((row) => row.repo).sort()).toEqual(landed.map(fullName).sort());
      for (const target of landed) {
        const merged = (await pulls(forge, target)).filter((pull) => pull.merged);
        expect(merged).toHaveLength(1);
        expect(done.landed_repos.find((row) => row.repo === fullName(target))?.commit_hash).toBe(merged[0]!.merge_commit_sha);
        expect(facts.find((fact) => fact.repo === fullName(target))?.commit_hash).toBe(merged[0]!.merge_commit_sha);
        const before = open.get(target.name);
        if (before !== undefined) expect(merged[0]!.number).toBe(before);
      }
    };
    /** The number of `repo`'s one open change, by repository name, when it has one. */
    const openChanges = async (...targets: Repository[]) => {
      const open = new Map<string, number>();
      for (const target of targets) {
        const rows = (await pulls(forge, target)).filter((pull) => pull.state === 'open');
        expect(rows.length).toBeLessThanOrEqual(1);
        if (rows.length === 1) open.set(target.name, rows[0]!.number);
      }
      return open;
    };

    const keys = appKeyPair();
    await forge.registerToken({ token: FIXTURE_TOKEN, token_kind: 'classic', scopes: ['repo'] });
    await forge.registerApp(APP, keys.publicKey);
    await forge.strictTokens();
    await secret(api, 'recovery-app-key', keys.privateKey);

    await test.step('credential-invalid: a token refused at deliver recovers once the forge accepts it again', async () => {
      await installed(api, forge, rejected, 'recovery-rejected', APP, 5001);
      const release = await held(rejected);
      const id = await promoted([rejected], 'recover a rejected token');
      await until(host, api, clock, id, (s) => s.pending !== null || settled(s));
      const lever = { repo: fullName(rejected) };
      expect((await forge.call('POST', '/_levers/token_rejected', lever)).status).toBe(200);
      await release();
      await escalated(id, rejected, 'recovery-rejected', 'credential-invalid');
      // The lever refuses every token on the repository, the harness's own too, so the change is read once it is lifted.
      expect((await forge.call('DELETE', `/_levers/token_rejected?repo=${encodeURIComponent(lever.repo)}`)).status).toBe(200);
      const open = await openChanges(rejected);
      expect(open.has(rejected.name)).toBe(true);
      await recovered(id, 'recovery-rejected', [rejected], open);
    });

    await test.step('permission-missing: withdrawn mid-delivery, it escalates with the change open and the earlier landing kept', async () => {
      await installed(api, forge, library, 'recovery-library', APP, 5002);
      await installed(api, forge, consumer, 'recovery-consumer', APP, 5003);
      const order = await api.put(`/api/projects/${project.slug}/landing-order`, { data: { repositories: [library.name, consumer.name] } });
      expect(order.status, JSON.stringify(order.body)).toBe(200);
      const release = await held(consumer);
      const id = await promoted([library, consumer], 'recover a permission withdrawn mid-delivery');
      // Both changes open and wait on the consumer's pending check; the library's lands at the pass that sees it pass.
      const opened = (s: Wire.ChunkDetail) => [library, consumer].every((repo) => s.artifacts.some((artifact) => artifact.name.startsWith(`delivery-pr/${fullName(repo)}/`)));
      const waiting = await until(host, api, clock, id, (s) => settled(s) || (s.pending !== null && opened(s)));
      expect(waiting.landed_repos).toEqual([]);
      await forge.setPermissions(5003, { ...FULL, contents: 'read' });
      await release();
      const state = await escalated(id, consumer, 'recovery-consumer', 'permission-missing');
      expect(state.escalation!.detail).toContain('Grant contents=write to the token or the App installation.');
      expect(state.landed_repos.map((row) => row.repo)).toEqual([fullName(library)]);
      const open = await openChanges(consumer);
      expect(open.has(consumer.name)).toBe(true);
      const branch = await forge.call('GET', `/repos/${fullName(consumer)}/git/ref/heads/${BRANCH}`);
      expect(branch.status, JSON.stringify(branch.body)).toBe(200);
      await forge.setPermissions(5003, FULL);
      await recovered(id, 'recovery-consumer', [library, consumer], open);
    });

    await test.step('token-unsupported: a fine-grained token on a private repository recovers once replaced with a classic token', async () => {
      const fine = 'github_pat_recovery-fine-grained-token';
      const classic = 'ghp_recovery-classic-token';
      await forge.registerToken({ token: fine, token_kind: 'fine-grained' });
      await forge.registerToken({ token: classic, token_kind: 'classic', scopes: ['repo'] });
      await forge.privateRepository(fullName(fineGrained));
      await tokened(api, fineGrained, 'recovery-fine-grained', fine);
      const id = await promoted([fineGrained], 'recover a fine-grained token on a private repository');
      await escalated(id, fineGrained, 'recovery-fine-grained', 'token-unsupported');
      const open = await openChanges(fineGrained);
      const replaced = await api.put('/api/secrets/recovery-fine-grained-secret/value', { data: { value: classic } });
      expect(replaced.status, JSON.stringify(replaced.body)).toBe(200);
      await recovered(id, 'recovery-fine-grained', [fineGrained], open);
    });

    await test.step('sso-required: a token refused behind SAML enforcement recovers once authorized', async () => {
      const token = 'ghp_recovery-sso-token';
      await forge.registerToken({ token, token_kind: 'classic', scopes: ['repo'] });
      await tokened(api, sso, 'recovery-sso', token);
      const release = await held(sso);
      const id = await promoted([sso], 'recover a token behind SSO');
      await until(host, api, clock, id, (s) => s.pending !== null || settled(s));
      const lever = { repo: fullName(sso) };
      expect((await forge.call('POST', '/_levers/sso_required', lever)).status).toBe(200);
      await release();
      await escalated(id, sso, 'recovery-sso', 'sso-required');
      expect((await forge.call('DELETE', `/_levers/sso_required?repo=${encodeURIComponent(lever.repo)}`)).status).toBe(200);
      const open = await openChanges(sso);
      expect(open.has(sso.name)).toBe(true);
      await recovered(id, 'recovery-sso', [sso], open);
    });

    await test.step("key-rejected: an App key replaced on the forge recovers once the original key is registered again", async () => {
      await forge.registerApp(REKEYED_APP, keys.publicKey);
      await installed(api, forge, rekeyed, 'recovery-rekeyed', REKEYED_APP, 6001);
      await forge.registerApp(REKEYED_APP, appKeyPair().publicKey);
      const id = await promoted([rekeyed], 'recover a replaced App key');
      await escalated(id, rekeyed, 'recovery-rekeyed', 'key-rejected');
      const open = await openChanges(rekeyed);
      await forge.registerApp(REKEYED_APP, keys.publicKey);
      await recovered(id, 'recovery-rekeyed', [rekeyed], open);
    });

    await test.step('installation-missing: a deleted installation recovers once it is installed again', async () => {
      await installed(api, forge, deleted, 'recovery-deleted', APP, 5004);
      await forge.deleteInstallation(5004);
      const id = await promoted([deleted], 'recover a deleted installation');
      await escalated(id, deleted, 'recovery-deleted', 'installation-missing');
      const open = await openChanges(deleted);
      await forge.registerInstallation({ installation_id: 5004, app_id: APP, repositories: [fullName(deleted)], permissions: FULL });
      await recovered(id, 'recovery-deleted', [deleted], open);
    });

    await test.step('installation-suspended: a suspended installation recovers once it is installed again', async () => {
      await installed(api, forge, suspended, 'recovery-suspended', APP, 5005);
      await forge.suspendInstallation(5005);
      const id = await promoted([suspended], 'recover a suspended installation');
      await escalated(id, suspended, 'recovery-suspended', 'installation-suspended');
      const open = await openChanges(suspended);
      await forge.registerInstallation({ installation_id: 5005, app_id: APP, repositories: [fullName(suspended)], permissions: FULL });
      await recovered(id, 'recovery-suspended', [suspended], open);
    });
  });
});
