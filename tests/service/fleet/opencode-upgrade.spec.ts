import { expect, type OpencodeRelease, stepChunkUntil, stepUntil, test } from '../../harness/fixtures';

// A machine moved between OpenCode releases, on a real hub: the runner's `opencode` is a host-owned path whose release
// the case replaces in place, as an operator's upgrade does. Moving to OpenCode 2 is stop, upgrade, start; a runner
// whose OpenCode changes major version under it holds the capability unavailable until it restarts.

const POOL = 'upgrade-pool';
const RUNNER = { runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${opencode}' } }] };

/** A turn the engine-backed OpenCode 2 runs: the runner composes prose around every turn's message, so it is tagged. */
function tagged(...lines: string[]): string {
  return ['<behavior-script>', ...lines, '</behavior-script>', ''].join('\n');
}

const OPENCODE1_BUILD = "tool_call('bash', {'command': 'true'}, output='built on OpenCode 1')\n";
const OPENCODE1_PASS = "verdict('pass', 'built on OpenCode 1')\n";
const OPENCODE2_REVIEW = tagged("text('REVIEWED on OpenCode 2')");
const OPENCODE2_PASS = tagged("text('<Choice>pass</Choice> the turn ran on OpenCode 2')");

/** Two runner nodes resuming one OpenCode session pool, each scripted for the release it runs on. */
function graph(build: { prompt: string; judgement: string }, review: { prompt: string; judgement: string }): string {
  const node = (step: { prompt: string; judgement: string }, to: string) => ({
    executor: 'runner',
    session: `resume:${POOL}`,
    prompt: step.prompt,
    judgement: { prompt: step.judgement, choices: { pass: { description: 'Ran.', to } } },
    retries: { max: 2, exhausted: 'escalate' },
  });
  return JSON.stringify({
    name: 'default-delivery',
    entry: 'build',
    sessions: { [POOL]: { harnesses: ['opencode'] } },
    nodes: { build: node(build, 'review'), review: node(review, 'done') },
  });
}

type World = Pick<Parameters<Parameters<typeof test>[2]>[0], 'api' | 'issue' | 'repository' | 'runnerHosts'>;

/** A host whose `opencode` starts as `release`, its tenant's graph `definition`, and a way to promote chunks onto it. */
async function world({ api, issue, repository, runnerHosts }: World, release: OpencodeRelease, definition: string) {
  const repo = await repository();
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! }, runnerConfig: RUNNER, runnerOpencode: release });
  expect((await api.post('/api/graphs', { data: { definition_yaml: definition } })).status).toBe(201);
  const runner = host.runners['runner-local']!;
  const health = async () => (await runner.harnessHealth()).body.items.find((item) => item.harness_id === 'opencode');
  /** Promotes a fresh chunk, and answers what a case reads it through. */
  const chunk = async (title: string) => {
    const { chunk: minted } = await issue(title, { repository: repo });
    expect((await api.post(`/api/chunks/${minted.chunk_id}/promote`)).status).toBe(202);
    const leases = async () => (await runner.leases()).body.items.filter((lease) => lease.chunk_id === minted.chunk_id).sort((a, b) => a.epoch - b.epoch);
    const settled = () => stepChunkUntil(host, api, minted.chunk_id, (status) => ['done', 'stopped', 'needs_human'].includes(status));
    return { leases, settled };
  };
  return { host, runner, health, chunk };
}

test.describe('opencode upgrade', () => {
  test.use({ isolation: 'fresh-tenant' });
  test.setTimeout(150_000);

  test('test_a_runner_stopped_on_opencode_1_and_started_on_opencode_2_carries_its_chunk_on_in_a_fresh_conversation', async ({ api, issue, repository, runnerHosts }) => {
    const definition = graph({ prompt: OPENCODE1_BUILD, judgement: OPENCODE1_PASS }, { prompt: OPENCODE2_REVIEW, judgement: OPENCODE2_PASS });
    const { host, runner, health, chunk } = await world({ api, issue, repository, runnerHosts }, 1, definition);
    const { leases, settled } = await chunk('opencode upgrade');
    const build = await stepUntil(host, async () => (await leases()).find((lease) => lease.node_name === 'build'), (found) => found !== undefined);
    // The judge's usage lands before a later pass closes build and mints review, so review is still unminted here.
    await stepUntil(host, async () => (await runner.leaseUsage(build!.lease_id)).body.items.some((fact) => fact.kind === 'judge'), Boolean);
    expect((await leases()).map((lease) => lease.node_name), 'review is minted only after the upgrade').toEqual(['build']);

    await host.stop();
    await host.installOpencode('2.1.4');
    await host.restart();

    expect(await settled()).toBe('done');
    expect((await health())?.available).toBe(true);
    const [built, missing, fresh, ...rest] = await leases();
    expect(rest).toEqual([]);
    expect(built).toMatchObject({ node_name: 'build', harness_id: 'opencode', harness_version: '1.18.25', closure_reason: 'transitioned' });
    // OpenCode 2 opens only a session its runner minted, so the pool's OpenCode 1 conversation is missing: that turn
    // fails as a lost conversation does, and the retry carries the node on in a conversation OpenCode 2 minted.
    expect(missing).toMatchObject({ node_name: 'review', harness_version: '2.1.4', session_id: built!.session_id, closure_reason: 'failed' });
    expect(fresh).toMatchObject({ node_name: 'review', harness_version: '2.1.4', closure_reason: 'transitioned' });
    expect(fresh!.session_id).toMatch(/^ses_/);
    const turns = (await runner.leaseTranscript(fresh!.lease_id)).body.turns;
    expect(turns.some((turn) => turn.kind === 'asst' && turn.text.includes('REVIEWED on OpenCode 2'))).toBe(true);
  });

  test('test_a_runner_whose_opencode_changes_major_version_while_running_is_unavailable_until_restarted', async ({ api, issue, repository, runnerHosts }) => {
    const definition = graph({ prompt: OPENCODE2_REVIEW, judgement: OPENCODE2_PASS }, { prompt: OPENCODE2_REVIEW, judgement: OPENCODE2_PASS });
    const { host, health, chunk } = await world({ api, issue, repository, runnerHosts }, '2.1.4', definition);
    const before = await chunk('before the upgrade');
    expect(await before.settled()).toBe('done');

    // The runner advertised 2.1.4 and ran on it; OpenCode moves to 3.0.0 beneath it, restarting its service.
    await host.installOpencode('3.0.0');
    const after = await chunk('after the upgrade');
    // The launch finds the service on another version and is refused before anything starts, failing its pass.
    const outcomes: string[] = [];
    while ((await health())?.available !== false && outcomes.length < 40) outcomes.push((await host.step()).outcome);
    expect(outcomes, 'the refused launch fails its pass').toContain('failed');
    const refused = await health();
    expect(refused, JSON.stringify(refused)).toMatchObject({ available: false, cause: 'incompatible_version', version: '3.0.0' });
    // Every launch on 3.0.0 was refused before a session was opened, so no turn ran on another major version.
    const refusedLeases = await after.leases();
    expect(refusedLeases.length).toBeGreaterThan(0);
    expect(refusedLeases.map((lease) => [lease.session_id, lease.harness_version]), JSON.stringify(refusedLeases)).toEqual(refusedLeases.map(() => [null, null]));

    // Back on an OpenCode 2 release, a restart composes the binding afresh: the capability is available, and work lands on it.
    await host.installOpencode('2.1.4');
    await host.restart();
    expect((await health())?.available).toBe(true);
    const restarted = await chunk('after the restart');
    expect(await restarted.settled()).toBe('done');
    expect((await restarted.leases()).map((lease) => [lease.node_name, lease.harness_version])).toEqual([['build', '2.1.4'], ['review', '2.1.4']]);
  });
});
