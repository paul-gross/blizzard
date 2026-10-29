import { expect, test } from '../../harness/fixtures';

// `blizzard-runner init` against a real hub that signs no one in: the operator's local-to-local bootstrap, and what a
// runner's hub-minted id survives: a rename, a deleted store, a retirement, and a reset of its hub's data. The hosts are
// stepped, so "online" and "renamed" are read after a completed step.

const NAME = 'runner-local';

const runnerIds = async (api: { get(route: string): Promise<{ body: Record<string, unknown> }> }): Promise<string[]> =>
  ((await api.get('/api/runners')).body.runners as { runner_id: string }[]).map((runner) => runner.runner_id);

test.describe('at fresh-tenant', () => {
  test.use({ isolation: 'fresh-tenant' });

  test.describe('a host that has not booted', () => {
    test.use({ runnerStarted: false });

    test('test_init_adds_a_runner_at_a_hub_without_sign_in_and_its_daemon_connects_under_the_minted_id', async ({ runnerHost, api, tenant }) => {
      const runner = runnerHost.runners[NAME]!;
      expect(runner.id.startsWith('rn_')).toBe(true);
      expect(runner.name).toBe(NAME);
      const added = await api.get(`/api/runners/${runner.id}`);
      expect(added.status).toBe(200);
      expect(added.body.connection).toBe('never_connected');

      await runnerHost.restart();
      expect((await runnerHost.step()).outcome).toBe('completed');
      const status = (await runner.status()).body;
      expect([status.runner_id, status.runner_name]).toEqual([runner.id, NAME]);
      expect((await api.get(`/api/runners/${runner.id}`)).body.connection).toBe('online');

      // Re-running init on an enrolled host keeps its tokens and adds nothing.
      const held = runnerHost.hubTokens();
      const again = await runnerHost.cli(['init', '--tenant', tenant.id]);
      expect(again.code, again.stderr).toBe(0);
      expect(runnerHost.hubTokens()).toEqual(held);
      expect(await runnerIds(api)).toEqual([runner.id]);
    });

    test('test_init_for_a_retired_runner_names_reinstate_and_leaves_the_token_untouched', async ({ runnerHost, api, tenant }) => {
      const runner = runnerHost.runners[NAME]!;
      const held = runnerHost.hubTokens();
      const retired = await api.post(`/api/runners/${runner.id}/retire`, { data: { by: 'operator', force: false } });
      expect(retired.status).toBe(200);

      const refused = await runnerHost.cli(['init', '--tenant', tenant.id]);
      expect(refused.code, refused.stdout).not.toBe(0);
      expect(refused.stdout + refused.stderr).toContain(`blizzard hub runner reinstate ${runner.id}`);
      expect(runnerHost.hubTokens()).toEqual(held);
      // The list leaves a retired runner out, so any runner it shows is one init added.
      expect(await runnerIds(api)).toEqual([]);
      expect((await api.get(`/api/runners/${runner.id}`)).body.retired).toBe(true);
    });
  });

  test('test_a_runner_renamed_in_its_config_shows_the_new_name_under_the_same_id_after_a_restart', async ({ runnerHost, api }) => {
    const id = runnerHost.runners[NAME]!.id;
    await runnerHost.stop();
    // Renaming a runner is editing its name and its token variable's name together.
    runnerHost.renameRunner(NAME, 'renamed-runner');
    await runnerHost.restart();
    expect((await runnerHost.step()).outcome).toBe('completed');

    const renamed = runnerHost.runners['renamed-runner']!;
    expect((await api.get(`/api/runners/${id}`)).body.runner_name).toBe('renamed-runner');
    const status = (await renamed.status()).body;
    expect([status.runner_id, status.runner_name]).toEqual([id, 'renamed-runner']);
    expect(await runnerIds(api)).toEqual([id]);
  });

  test('test_a_runner_whose_store_is_deleted_re_registers_under_the_same_id', async ({ runnerHost, api, bearer, tenant }) => {
    const id = runnerHost.runners[NAME]!.id;
    const token = runnerHost.hubTokens().runners[NAME]!;
    const identity = async (): Promise<unknown> => (await (await bearer(token)).get('/api/fleet/identity')).body;
    const known = await identity();
    await runnerHost.stop();
    runnerHost.removeStore();

    // Init rebuilds the store and keeps the token, which the hub still knows.
    const rebuilt = await runnerHost.cli(['init', '--tenant', tenant.id]);
    expect(rebuilt.code, rebuilt.stderr).toBe(0);
    expect(await identity()).toEqual(known);
    await runnerHost.restart();
    expect((await runnerHost.step()).outcome).toBe('completed');
    expect((await runnerHost.runners[NAME]!.status()).body.runner_id).toBe(id);
    expect(await runnerIds(api)).toEqual([id]);
  });
});

test.describe('at fresh-hub', () => {
  test.use({ isolation: 'fresh-hub', runnerStarted: false });

  test('test_after_its_hub_s_data_is_reset_init_re_adds_the_runner_only_with_allow_readd', async ({ runnerHost, freshHub, testHub, anonymous, bearer }) => {
    const first = runnerHost.runners[NAME]!;
    const firstToken = runnerHost.hubTokens().runners[NAME]!;
    const held = runnerHost.hubTokens();
    await freshHub.stop();
    // The same hub URL over fresh data: the token the runner holds is unknown there. The reset hub's one tenant has a
    // new id, so `init` takes none, and the reads settle on that sole tenant.
    await freshHub.reset();

    const refused = await runnerHost.cli(['init']);
    expect(refused.code, refused.stdout).not.toBe(0);
    const message = refused.stdout + refused.stderr;
    expect(message).toContain(testHub.coordinates.url);
    expect(message).toContain('--allow-readd');
    expect(runnerHost.hubTokens()).toEqual(held);
    expect(((await anonymous.get('/api/runners')).body.runners as unknown[]).length).toBe(0);

    const readded = await runnerHost.cli(['init', '--allow-readd']);
    expect(readded.code, readded.stderr).toBe(0);
    const tokens = runnerHost.hubTokens();
    const token = tokens.runners[NAME]!;
    expect(token).not.toBe(firstToken);
    const identity = await (await bearer(token)).get('/api/fleet/identity');
    const newId = identity.body.runner_id as string;
    expect(newId.startsWith('rn_')).toBe(true);
    expect(newId).not.toBe(first.id);
    expect((await (await bearer(firstToken)).get('/api/fleet/identity')).status).toBe(401);
    const listed = (await anonymous.get('/api/runners')).body.runners as { runner_id: string }[];
    expect(listed.map((runner) => runner.runner_id)).toEqual([newId]);
  });
});
