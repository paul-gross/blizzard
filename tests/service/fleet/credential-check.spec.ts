import { appKeyPair, expect, FIXTURE_FORGE, FIXTURE_TOKEN, test, type HubApi, type MockJournal, type Repository, type Wire } from '../../harness/fixtures';

// A credential's read-only check, end to end, under the mock forge's `strict_tokens`: only a registered token or a
// token the hub minted for an installation authenticates, so every `proven` read went to the forge under the
// credential's own token. The forge's request journal shows what the check sent; nothing but reads and the mint does.

const APP = 301;
/** Every permission the landing and work-source scopes need or use. */
const FULL = { metadata: 'read', contents: 'write', pull_requests: 'write', issues: 'write', checks: 'read', statuses: 'read', actions: 'write' } as const;
/** The operations a repository use proves by reading; every other operation is judged from the grant. */
const READS = new Set(['access', 'refs.read', 'compare', 'commits.read', 'changes.read', 'checks.read']);
/** The seconds the forge holds each request to the slow repository: two calls fit the check's 60 s cap, a third does not. */
const LATENCY = 25;
const OUTCOMES = ['proven', 'granted', 'unproven', 'refused', 'unsupported'] as const;

const fullName = (repo: Repository) => `${repo.owner}/${repo.name}`;

async function secret(api: HubApi, name: string, value: string) {
  const created = await api.post('/api/secrets', { data: { name, value } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
}

/** Creates the credential `slug` on the fixture forge; answers its id. */
async function credential(api: HubApi, slug: string, binding: string, secretName: string, settings: Record<string, unknown> = {}): Promise<string> {
  const created = await api.post('/api/credentials', { data: { slug, forge: FIXTURE_FORGE, binding, secret: secretName, settings } });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  return created.body.credential_id as string;
}

/** Rebinds `repo`'s repository record to the credential `slug`, and its work source too when `source`. */
async function bind(api: HubApi, repo: Repository, slug: string, source = false) {
  const repository = await api.patch(`/api/repositories/${repo.name}`, { data: { credential: slug } });
  expect(repository.status, JSON.stringify(repository.body)).toBe(200);
  if (!source) return;
  const sourced = await api.patch(`/api/work-sources/${repo.tokenPrefix}`, { data: { credential: slug, annotate: true } });
  expect(sourced.status, JSON.stringify(sourced.body)).toBe(200);
}

/** Checks the credential `slug`, the answer `200`; `scope` names the uses, every enabled one when absent. */
async function check(api: HubApi, slug: string, scope: Wire.CredentialCheckRequest = {}): Promise<Wire.CredentialCheckView> {
  const answer = await api.post<Wire.CredentialCheckView>(`/api/credentials/${slug}/checks`, { data: scope, timeout: 90_000 });
  expect(answer.status, JSON.stringify(answer.body)).toBe(200);
  return answer.body;
}

/** The operations of the one use of `kind` named `slug`, by operation. */
function operations(view: Wire.CredentialCheckView, kind: string, slug: string): Map<string, Wire.OperationResultView> {
  const uses = view.uses.filter((use) => use.kind === kind && use.slug === slug);
  expect(uses, JSON.stringify(view.uses)).toHaveLength(1);
  expect(uses[0]!.reached).toBe(true);
  return new Map(uses[0]!.operations.map((operation) => [operation.operation, operation]));
}

/** How many operations, the mint among them, ended in each outcome. */
function counts(view: Wire.CredentialCheckView): Record<string, number> {
  const all = [...(view.mint ? [view.mint] : []), ...view.uses.flatMap((use) => use.operations)];
  return Object.fromEntries(OUTCOMES.map((outcome) => [outcome, all.filter((operation) => operation.outcome === outcome).length]));
}

test.describe('at fresh-hub', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled' });

  test('test_a_credential_check_reads_only_and_answers_each_outcome_within_its_cap', async ({ api, forge, repository }) => {
    test.slow();
    const installed = await repository();
    const classic = await repository();
    const fineGrained = await repository();
    const checksless = await repository();
    const slow = [await repository(), await repository(), await repository()];
    /** Each check's credential id and what its answer counted, for the events. */
    const checked: { credential_id: string; counts: Record<string, number> }[] = [];

    const keys = appKeyPair();
    await forge.registerToken({ token: FIXTURE_TOKEN, token_kind: 'classic', scopes: ['repo'] });
    await forge.registerApp(APP, keys.publicKey);
    await forge.strictTokens();
    await secret(api, 'check-app-key', keys.privateKey);

    await test.step('an installation is checked with reads and the mint alone, its writes granted', async () => {
      await forge.registerInstallation({ installation_id: 3001, app_id: APP, repositories: [fullName(installed)], permissions: FULL });
      const id = await credential(api, 'check-app', 'github.app-installation', 'check-app-key', { app_id: APP, installation_id: 3001 });
      await bind(api, installed, 'check-app', true);
      expect((await forge.call('DELETE', '/_journal')).status).toBe(200);
      const view = await check(api, 'check-app');
      const journal = (await forge.call('GET', '/_journal')).body as MockJournal;
      const mint = /^\/app\/installations\/3001\/access_tokens$/;
      expect(journal.entries.filter((entry) => entry.method !== 'GET' && !(entry.method === 'POST' && mint.test(entry.path))), JSON.stringify(journal.entries)).toEqual([]);
      expect(journal.entries.filter((entry) => entry.method === 'POST')).not.toEqual([]);
      expect(journal.entries.filter((entry) => entry.method === 'GET')).not.toEqual([]);

      expect(view.mint?.outcome).toBe('proven');
      expect(view.granted_permissions).toEqual(expect.objectContaining({ contents: 'write', checks: 'read' }));
      expect(view.not_evaluated).toBe('Branch protection, required reviews, and rulesets are not evaluated.');
      for (const [name, operation] of operations(view, 'repository', installed.name)) {
        expect(operation.outcome, `${name}: ${operation.detail}`).toBe(READS.has(name) ? 'proven' : 'granted');
      }
      for (const [name, operation] of operations(view, 'work_source', installed.tokenPrefix)) {
        expect(operation.outcome, `${name}: ${operation.detail}`).toBe(name === 'issues.read' ? 'proven' : 'granted');
      }
      checked.push({ credential_id: id, counts: counts(view) });
    });

    await test.step('a classic repo token proves its reads and is granted its writes', async () => {
      const token = 'ghp_check-classic-token';
      await forge.registerToken({ token, token_kind: 'classic', scopes: ['repo'] });
      await secret(api, 'check-classic-secret', token);
      const id = await credential(api, 'check-classic', 'github.token', 'check-classic-secret', { variant: 'classic' });
      await bind(api, classic, 'check-classic');
      const view = await check(api, 'check-classic', { repositories: [classic.name] });
      expect(view.mint).toBeNull();
      expect(view.variant).toBe('classic');
      expect(view.uses).toHaveLength(1);
      const answered = operations(view, 'repository', classic.name);
      expect([...answered.keys()]).toEqual(expect.arrayContaining(['access', 'refs.read', 'changes.read', 'checks.read']));
      for (const [name, operation] of answered) {
        expect(operation.outcome, `${name}: ${operation.detail}`).toBe(READS.has(name) ? 'proven' : 'granted');
      }
      checked.push({ credential_id: id, counts: counts(view) });
    });

    await test.step("a fine-grained token's writes are unproven, and a private repository's check runs are refused to it", async () => {
      const token = 'github_pat_check-fine-grained-token';
      await forge.registerToken({ token, token_kind: 'fine-grained' });
      await forge.privateRepository(fullName(fineGrained));
      await secret(api, 'check-fine-grained-secret', token);
      const id = await credential(api, 'check-fine-grained', 'github.token', 'check-fine-grained-secret');
      await bind(api, fineGrained, 'check-fine-grained');
      const view = await check(api, 'check-fine-grained');
      expect(view.variant).toBe('fine-grained');
      const answered = operations(view, 'repository', fineGrained.name);
      expect(answered.get('access')?.outcome).toBe('proven');
      const runs = answered.get('checks.read');
      expect(runs?.outcome).toBe('refused');
      expect(runs?.detail).toContain('Use a classic token or a GitHub App');
      expect(runs?.diagnostic?.status).toBe(403);
      for (const [name, operation] of answered) {
        if (!READS.has(name)) expect(operation.outcome, `${name}: ${operation.detail}`).toBe('unproven');
      }
      checked.push({ credential_id: id, counts: counts(view) });
    });

    await test.step('an installation without checks answers checks.read unsupported, no call made', async () => {
      const { checks: _checks, statuses: _statuses, ...withoutChecks } = FULL;
      await forge.registerInstallation({ installation_id: 3002, app_id: APP, repositories: [fullName(checksless)], permissions: withoutChecks });
      const id = await credential(api, 'check-checksless', 'github.app-installation', 'check-app-key', { app_id: APP, installation_id: 3002 });
      await bind(api, checksless, 'check-checksless');
      const view = await check(api, 'check-checksless');
      const answered = operations(view, 'repository', checksless.name);
      expect(answered.get('checks.read')?.outcome).toBe('unsupported');
      expect(answered.get('access')?.outcome).toBe('proven');
      checked.push({ credential_id: id, counts: counts(view) });
    });

    await test.step('a named repository another credential acts for is refused, and no check is recorded', async () => {
      const refused = await api.post(`/api/credentials/check-classic/checks`, { data: { repositories: [checksless.name] } });
      expect(refused.status, JSON.stringify(refused.body)).toBe(422);
      expect(refused.body.code).toBe('credential_not_used_by');
      expect(JSON.stringify(refused.body.uses)).toContain(checksless.name);
    });

    await test.step('a slow forge ends the check at its cap, every use past it unreached', async () => {
      const token = 'ghp_check-slow-token';
      await forge.registerToken({ token, token_kind: 'classic', scopes: ['repo'] });
      await secret(api, 'check-slow-secret', token);
      const id = await credential(api, 'check-slow', 'github.token', 'check-slow-secret', { variant: 'classic' });
      for (const repo of slow) await bind(api, repo, 'check-slow');
      const unhindered = await check(api, 'check-slow');
      checked.push({ credential_id: id, counts: counts(unhindered) });
      const order = unhindered.uses.map((use) => use.slug);
      expect(order).toHaveLength(3);
      const first = slow.find((repo) => repo.name === order[0])!;
      const lever = { repo: fullName(first), seconds: LATENCY };
      expect((await forge.call('POST', '/_levers/latency', lever)).status).toBe(200);
      try {
        // The cap is real elapsed time, not the stack's controlled clock.
        // eslint-disable-next-line no-restricted-syntax
        const started = Date.now();
        const view = await check(api, 'check-slow');
        // eslint-disable-next-line no-restricted-syntax
        const elapsed = (Date.now() - started) / 1000;
        expect(elapsed).toBeGreaterThanOrEqual(55);
        expect(elapsed).toBeLessThan(70);
        expect(view.uses.map((use) => [use.slug, use.reached])).toEqual(order.map((slug) => [slug, false]));
        checked.push({ credential_id: id, counts: counts(view) });
      } finally {
        expect((await forge.call('DELETE', `/_levers/latency?repo=${encodeURIComponent(lever.repo)}`)).status).toBe(200);
      }
    });

    await test.step('each check records one credential-check event counting its outcomes', async () => {
      type Event = { kind: string; message: string; detail?: { credential_id?: string; counts?: Record<string, number> } };
      const feed = (await api.get<{ events: Event[] }>('/api/events?limit=200')).body.events;
      const recorded = feed.filter((event) => event.kind === 'credential-check').map((event) => ({ credential_id: event.detail?.credential_id, counts: event.detail?.counts }));
      const key = (entry: { credential_id?: string; counts?: Record<string, number> }) => JSON.stringify([entry.credential_id, entry.counts]);
      expect(recorded.map(key).sort()).toEqual(checked.map(key).sort());
    });
  });
});
