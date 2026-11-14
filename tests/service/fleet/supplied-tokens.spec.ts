import { chunkProgress, expect, FIXTURE_FORGE, FIXTURE_TOKEN, stepUntil, test, type ForgeApi, type HubApi, type Repository, type RunnerHost, type Wire } from '../../harness/fixtures';

// Supplied forge tokens, end to end, under the mock forge's `strict_tokens`: only a registered token authenticates, so a
// pass that goes through proves the hub sent the token its credential names. Every scenario runs on one hub with no
// restart. The fixture token stays registered as classic throughout, so the fixture's own forge reads keep working.

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
          "subprocess.run(['git', '-C', repo, 'switch', '-C', 'feature/supplied-token'], check=True)",
          "(pathlib.Path(repo) / 'SUPPLIED_TOKEN.md').write_text('landed with a supplied token\\n')",
          "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True)",
          "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: a change landed with a supplied token'], check=True)",
          "sha = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
          "subprocess.run(['git', '-C', repo, 'push', 'origin', 'feature/supplied-token'], check=True)",
          "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feature/supplied-token', '--commit', sha], check=True)",
          '',
        ].join('\n'),
        judgement: { prompt: "verdict('pass', 'committed the change')\n", choices: { pass: { description: 'Committed.', to: 'deliver' } } },
        retries: { max: 2, exhausted: 'escalate' },
      },
      deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 600, judgement: { choices: {
        landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
        failure: { description: 'Failed.', to: 'build' }, 'inherited-failure': { description: 'Base check failed.', to: 'build' },
      } } },
    },
  };
}

/** A `github.token` credential on the fixture forge reading a new secret whose value is `token`. */
async function credential(api: HubApi, slug: string, token: string, settings: Record<string, string> = {}) {
  const secret = await api.post('/api/secrets', { data: { name: `${slug}-secret`, value: token } });
  expect(secret.status, JSON.stringify(secret.body)).toBe(201);
  const created = await api.post('/api/credentials', {
    data: { slug, forge: FIXTURE_FORGE, binding: 'github.token', secret: `${slug}-secret`, settings },
  });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
}

/** Rebinds `repo`'s repository record, and its work source unless `repositoryOnly`, to the credential `slug`. */
async function bind(api: HubApi, repo: Repository, slug: string, repositoryOnly = false) {
  const repository = await api.patch(`/api/repositories/${repo.name}`, { data: { credential: slug } });
  expect(repository.status, JSON.stringify(repository.body)).toBe(200);
  if (repositoryOnly) return;
  const source = await api.patch(`/api/work-sources/${repo.tokenPrefix}`, { data: { credential: slug } });
  expect(source.status, JSON.stringify(source.body)).toBe(200);
}

type Pull = { number: number; merged: boolean };
async function pulls(forge: ForgeApi, repo: Repository): Promise<Pull[]> {
  const result = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/pulls?state=all`);
  expect(result.status).toBe(200);
  return result.body as Pull[];
}

/** Steps the host until `reached` holds or the chunk waits at the hub's `deliver` node, then reads the hub until it holds. */
async function drive(host: RunnerHost, api: HubApi, id: string, reached: (state: Wire.ChunkDetail) => boolean) {
  const read = async () => (await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`)).body;
  let state = await stepUntil(host, read, (s) => reached(s) || s.pending !== null, { progress: chunkProgress });
  await expect.poll(async () => reached((state = await read())), { timeout: 60000 }).toBe(true);
  return state;
}

const settled = (s: Wire.ChunkDetail) => ['done', 'needs_human'].includes(s.status);

test.describe('at fresh-hub', () => {
  test.use({ isolation: 'fresh-hub' });

  test('test_supplied_tokens_authenticate_land_refuse_and_rebind_under_strict_tokens', async ({ api, forge, issue, repository, runnerHosts }) => {
    test.slow();
    const classic = await repository();
    const fineGrained = await repository();
    const revoked = await repository();
    const rebound = await repository();
    const repos = [classic, fineGrained, revoked, rebound];
    const host = await runnerHosts.create({ runnerRepositories: Object.fromEntries(repos.map((r) => [r.name, r.origin!])) });
    const graphs = new Map<string, string>();
    for (const [index, repo] of repos.entries()) {
      const created = await api.post('/api/graphs', { data: { definition_yaml: JSON.stringify(graph(repo, index === 0 ? 'default-delivery' : `delivery-${repo.name}`)) } });
      expect(created.status, JSON.stringify(created.body)).toBe(201);
      graphs.set(repo.name, created.body.graph_id as string);
    }
    /** Files and ingests an issue in `repo`, puts it on that repository's graph, and promotes it. */
    const promoted = async (repo: Repository, title: string) => {
      const { chunk } = await issue(title, { repository: repo });
      expect((await api.patch(`/api/chunks/${chunk.chunk_id}`, { data: { graph_id: graphs.get(repo.name) } })).status).toBe(202);
      expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
      return chunk.chunk_id;
    };
    const events = async (id: string) =>
      (await api.get<{ events: { kind: string; chunk_id: string | null; detail?: Record<string, unknown> }[] }>(`/api/events?chunk_id=${id}&limit=200`)).body.events;

    await forge.registerToken({ token: FIXTURE_TOKEN, token_kind: 'classic', scopes: ['repo'] });
    await forge.strictTokens();

    await test.step('a classic token lands a private repository through merge-on-checks', async () => {
      const token = 'ghp_classic-supplied-token';
      await forge.registerToken({ token, token_kind: 'classic', scopes: ['repo'] });
      await forge.privateRepository(`${classic.owner}/${classic.name}`);
      await credential(api, 'classic-pat', token, { variant: 'classic' });
      await bind(api, classic, 'classic-pat');
      const id = await promoted(classic, 'land with a classic token');
      const done = await drive(host, api, id, settled);
      expect(done.status, JSON.stringify(done.escalation)).toBe('done');
      expect(done.landed).toBe(true);
      expect((await pulls(forge, classic)).filter((pull) => pull.merged)).toHaveLength(1);
    });

    await test.step('a fine-grained token escalates forge-capability-missing before any forge write', async () => {
      // The carried-over shape: a `github.token` credential with no variant, which the hub derives from the prefix.
      const token = 'github_pat_fine-grained-supplied-token';
      await forge.registerToken({ token, token_kind: 'fine-grained' });
      await forge.privateRepository(`${fineGrained.owner}/${fineGrained.name}`);
      await credential(api, 'fine-grained-pat', token);
      await bind(api, fineGrained, 'fine-grained-pat', true);
      const id = await promoted(fineGrained, 'refuse a fine-grained token');
      const escalated = await drive(host, api, id, settled);
      expect(escalated.status).toBe('needs_human');
      expect(escalated.escalation!.cause).toBe('forge-capability-missing');
      expect(escalated.current_node_name).toBe('deliver');
      const missing = (await events(id)).filter((event) => event.kind === 'forge-capability-missing');
      expect(missing).toHaveLength(1);
      expect(missing[0]!.detail?.credential).toBe('fine-grained-pat');
      expect(await pulls(forge, fineGrained)).toEqual([]);
    });

    await test.step('a revoked token escalates forge-auth naming its credential', async () => {
      const token = 'ghp_revoked-supplied-token';
      await forge.registerToken({ token, token_kind: 'classic', scopes: ['repo'] });
      await credential(api, 'revoked-pat', token, { variant: 'classic' });
      await bind(api, revoked, 'revoked-pat', true);
      const lever = { repo: `${revoked.owner}/${revoked.name}` };
      expect((await forge.call('POST', '/_levers/checks_pending', lever)).status).toBe(200);
      const id = await promoted(revoked, 'refuse a revoked token');
      await drive(host, api, id, (s) => s.pending !== null);
      await forge.revokeToken(token);
      expect((await forge.call('DELETE', `/_levers/checks_pending?repo=${encodeURIComponent(lever.repo)}`)).status).toBe(200);
      const escalated = await drive(host, api, id, settled);
      expect(escalated.status).toBe('needs_human');
      expect(escalated.escalation!.cause).toBe('forge-auth');
      expect(escalated.escalation!.detail || '').toContain('revoked-pat');
      expect(escalated.landed).toBe(false);
      const refused = (await events(id)).filter((event) => event.kind === 'forge-auth-refused');
      expect(refused.map((event) => event.chunk_id)).toEqual([id]);
      expect((await pulls(forge, revoked)).some((pull) => pull.merged)).toBe(false);
    });

    await test.step('rebinding a repository and its source sends the new token at the next request', async () => {
      const first = 'ghp_rebound-first-token';
      const second = 'ghp_rebound-second-token';
      await forge.registerToken({ token: first, token_kind: 'classic', scopes: ['repo'] });
      await forge.registerToken({ token: second, token_kind: 'classic', scopes: ['repo'] });
      await credential(api, 'rebound-first', first, { variant: 'classic' });
      await credential(api, 'rebound-second', second, { variant: 'classic' });
      await bind(api, rebound, 'rebound-first');
      await bind(api, rebound, 'rebound-second');
      // Only the second token authenticates from here, so the ingest and the landing prove the hub sent it.
      await forge.revokeToken(first);
      const id = await promoted(rebound, 'land after a rebinding');
      // A refused read still ingests, recording its error on the work item, so the source's token is proven here.
      const items = (await api.get(`/api/chunks/${id}/work-items`)).body as { items: { error: string | null }[] };
      expect(items.items.map((item) => item.error)).toEqual([null]);
      const done = await drive(host, api, id, settled);
      expect(done.status, JSON.stringify(done.escalation)).toBe('done');
      expect(done.landed).toBe(true);
      expect((await pulls(forge, rebound)).filter((pull) => pull.merged)).toHaveLength(1);
    });
  });
});
