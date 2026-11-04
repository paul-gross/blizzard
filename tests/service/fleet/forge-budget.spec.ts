import { chunkProgress, expect, stepUntil, test, type HubApi, type Repository, type Wire } from '../../harness/fixtures';

type Clock = Parameters<Parameters<typeof test>[2]>[0]['clock'];

test.use({ isolation: 'fresh-hub', clockMode: 'controlled' });

/** The forge-status full-pass floor, `FULL_PASS_FLOOR_INTERVALS` (5) × `DEFAULT_ANNOTATION_INTERVAL_SECONDS` (120). */
const FLOOR_SECONDS = 600;
/** The most an instant read off the stack and one rounded by a daemon may differ by. */
const TOLERANCE_MS = 1;

function graph(repo: Repository): string {
  const prompt = [
    'import os, pathlib, subprocess',
    `name = ${JSON.stringify(repo.name)}`,
    "path = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name",
    "subprocess.run(['git', '-C', str(path), 'switch', '-C', 'feature/budget'], check=True)",
    "(path / 'LANDED.md').write_text('landed\\n')",
    "subprocess.run(['git', '-C', str(path), 'add', '-A'], check=True)",
    "subprocess.run(['git', '-C', str(path), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: budget'], check=True)",
    "sha = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    "subprocess.run(['git', '-C', str(path), 'push', 'origin', 'feature/budget'], check=True)",
    "subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feature/budget', '--commit', sha], check=True)",
  ].join('\n');
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt, judgement: { prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: 600, judgement: { choices: {
      landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
      failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base failure.', to: 'build' },
    } } },
  } });
}

async function detail(api: HubApi, id: string): Promise<Wire.ChunkDetail> {
  const response = await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`);
  expect(response.status).toBe(200);
  return response.body;
}

/**
 * One turn of the flow driver on a controlled hub, which runs no timer of its own. With `due`, the stack's clock first
 * moves to the pending visit's own `next_poll_at`, when that lies ahead; without it the stack stands still.
 */
async function flowTurn(api: HubApi, clock: Pick<Clock, 'now' | 'set'>, id: string, due: boolean): Promise<Wire.ChunkDetail> {
  const row = await detail(api, id);
  const next = row.pending?.next_poll_at ? Date.parse(row.pending.next_poll_at) : NaN;
  if (due && next > clock.now().getTime()) await clock.set(new Date(next));
  await api.flowStep();
  return detail(api, id);
}

test('test_a_low_budget_turns_the_sweep_away_while_a_landing_proceeds', async ({ api, clock, forge, repository, issue, runnerHosts }) => {
  test.setTimeout(180_000);
  const repo = await repository();
  const name = `${repo.owner}/${repo.name}`;
  const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
  expect((await api.patch(`/api/work-sources/${repo.tokenPrefix}`, { data: { annotate: true } })).status).toBe(200);
  expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repo) } })).status).toBe(201);
  const sweep = (kind: 'forge_status' | 'close_drain') => api.sweep(kind, { allTenants: true });
  const labels = async (number: number) => {
    const result = await forge.call('GET', `/repos/${name}/issues/${number}`);
    expect(result.status).toBe(200);
    return (result.body as { labels: { name: string }[] }).labels.map((label) => label.name).sort();
  };
  const budget = async () => {
    const list = await api.get<Wire.ForgesListView>('/api/forges');
    const forgeId = list.body.forges[0]!.forge_id;
    const data = await api.get<Wire.ForgeDetail>(`/api/forges/${forgeId}`);
    return data.body.budgets.find((entry) => entry.secret_name === 'fixture-forge-token' && entry.bucket === 'core');
  };
  const first = await issue('labelled while budget is healthy', { repository: repo });
  await sweep('forge_status');
  expect(await labels(first.issue.number)).toEqual(['blizzard:ingested']);
  const reset = Math.floor(clock.now().getTime() / 1000) + 900;
  expect((await forge.call('POST', '/_levers/rate_budget', { remaining: 1500, reset })).status).toBe(200);
  // Only a full pass teaches the budget, and the next one comes once the floor has passed; the advance stays short of the reset.
  await clock.advance(FLOOR_SECONDS);
  await sweep('forge_status');
  expect((await budget())?.remaining).toBeLessThanOrEqual(1500);
  await sweep('forge_status');
  const low = await budget();
  expect(low?.limit).toBe(5000);
  expect(Date.parse(low!.reset)).toBe(reset * 1000);
  const second = await issue('never labelled while budget is low', { repository: repo });
  expect((await api.post(`/api/chunks/${second.chunk.chunk_id}/promote`)).status).toBe(202);
  await sweep('forge_status');
  await sweep('forge_status');
  expect(await labels(second.issue.number)).toEqual([]);
  expect(await budget()).toEqual(low);
  const done = await stepUntil(host, () => flowTurn(api, clock, second.chunk.chunk_id, true), (row) => ['done', 'stopped', 'needs_human'].includes(row.status), { stallPasses: 80, progress: chunkProgress });
  expect(done.status).toBe('done');
  expect(await host.originTree(repo.name, 'main')).toContain('LANDED.md');
  expect((await budget())!.remaining).toBeLessThan(low!.remaining);
  await sweep('close_drain');
  const diagnostic = async () => (await api.get<Wire.CloseDiagnostic>(`/api/chunks/${second.chunk.chunk_id}/close-diagnostic`)).body;
  expect((await diagnostic()).attempts).toHaveLength(1);
  const closed = await diagnostic();
  expect(closed.attempts.map((attempt) => attempt.outcome)).toEqual(['deferred']);
  expect(Date.parse(closed.attempts[0]!.resume_at!)).toBe(reset * 1000);
  await sweep('close_drain');
  const after = await diagnostic();
  expect(after.attempts).toEqual(closed.attempts);
  expect(after.closures).toEqual([]);
  expect(after.intents.filter((intent) => intent.retired_at === null)).toHaveLength(1);
  expect((await forge.call('GET', `/repos/${name}/issues/${second.issue.number}`)).body.state).toBe('open');
  const events = (await api.get<{ events: { kind: string; severity: string }[] }>('/api/events?limit=200')).body.events;
  expect(events.filter((event) => event.kind === 'forge-budget-low').map((event) => event.severity)).toEqual(['warning']);
  expect(events.filter((event) => event.kind === 'forge-rate-limited')).toEqual([]);
});

for (const lever of ['rate_limited', 'secondary_rate_limited']) {
  test(`test_a_rate_refused_landing_pends_until_the_refusal_lifts_then_lands[${lever}]`, async ({ api, clock, forge, repository, issue, runnerHosts }) => {
    test.setTimeout(240_000);
    const repo = await repository();
    const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph(repo) } })).status).toBe(201);
    const { chunk } = await issue(`land past a ${lever} refusal`, { repository: repo });
    expect((await api.post(`/api/chunks/${chunk.chunk_id}/promote`)).status).toBe(202);
    const reset = Math.floor(clock.now().getTime() / 1000) + 90;
    expect((await forge.call('POST', '/_levers/rate_budget', { reset })).status).toBe(200);
    const name = `${repo.owner}/${repo.name}`;
    expect((await forge.call('POST', `/_levers/${lever}`, { repo: name })).status).toBe(200);
    // The stack stands still from the lever's arming to the refusal, so the refusal's window is exactly its 60 seconds.
    const refusedAt = clock.now().getTime();
    const pending = await stepUntil(host, () => flowTurn(api, clock, chunk.chunk_id, false), (row) => row.pending !== null, { stallPasses: 80, progress: chunkProgress });
    expect(clock.now().getTime()).toBe(refusedAt);
    expect(pending.status).toBe('delivering');
    expect(pending.landed).toBe(false);
    expect(await host.originTree(repo.name, 'main')).not.toContain('LANDED.md');
    const retry = Date.parse(pending.pending!.next_poll_at!);
    expect(Math.abs(retry - (refusedAt + 60_000))).toBeLessThanOrEqual(TOLERANCE_MS);
    expect((await forge.call('DELETE', `/_levers/${lever}?repo=${encodeURIComponent(name)}`)).status).toBe(200);
    let lastPoll = retry;
    await stepUntil(host, async () => {
      const row = await flowTurn(api, clock, chunk.chunk_id, true);
      if (row.pending?.next_poll_at) lastPoll = Date.parse(row.pending.next_poll_at);
      return row;
    }, (row) => row.status === 'done', { stallPasses: 8, progress: chunkProgress });
    const lift = lever === 'rate_limited' ? reset * 1000 : retry;
    expect(Math.abs(lastPoll - lift)).toBeLessThanOrEqual(TOLERANCE_MS);
    expect(clock.now().getTime()).toBeGreaterThanOrEqual(lift - TOLERANCE_MS);
    expect(await host.originTree(repo.name, 'main')).toContain('LANDED.md');
    const events = (await api.get<{ events: { kind: string; severity: string }[] }>('/api/events?limit=200')).body.events;
    expect(events.filter((event) => event.kind === 'forge-rate-limited').map((event) => event.severity)).toEqual(['warning']);
  });
}
