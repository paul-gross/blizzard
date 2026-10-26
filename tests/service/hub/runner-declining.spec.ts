import { expect, type RunnerDeclining, test } from '../../harness/fixtures';

// A runner's declining report over a real hub: a registration naming none reads as declining nothing; one naming
// reasons shows them, superseded whole by the next registration. The test mints a runner, so it owns its tenant.
test.use({ isolation: 'fresh-tenant' });

const CAPABILITIES = [{ harness_id: 'claude', default: true }];
const FLOOR: RunnerDeclining = { lever: 'floor', detail: 'exhausted', subscription: 'anthropic', until: '2026-10-22T14:00:00Z' };
const PACE: RunnerDeclining = { lever: 'pace', detail: '7d 86% spent, 71% elapsed' };

test('test_a_hub_shows_the_reasons_a_runner_reports_and_nothing_from_one_that_predates_the_field', async ({ api, fleet }) => {
  const runner = await fleet('older-runner');
  const register = async (declared: { declining?: RunnerDeclining[] } = {}) => {
    const registered = await runner.post('/api/fleet/runners', { data: { workspace_id: 'w1', capabilities: CAPABILITIES, ...declared } });
    expect(registered.status).toBe(201);
  };
  /** The reasons the operator registry shows the runner declining on. */
  const held = async (): Promise<{ lever: string; subscription: string | null; until: string | null }[]> => {
    const registry = await api.get(`/api/runners/${runner.runnerId}`);
    expect(registry.status).toBe(200);
    return registry.body.declining;
  };

  await register();
  expect(await held()).toEqual([]);

  await register({ declining: [FLOOR, PACE] });
  expect((await held()).map((reason) => [reason.lever, reason.subscription, reason.until])).toEqual([
    ['floor', 'anthropic', '2026-10-22T14:00:00+00:00'],
    ['pace', null, null],
  ]);

  await register({ declining: [PACE] });
  expect((await held()).map((reason) => reason.lever)).toEqual(['pace']);

  await register();
  expect(await held()).toEqual([]);
});
