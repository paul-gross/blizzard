import { chunkProgress, expect, stepUntil, test, type ForgeApi, type HubApi, type Repository, type RunnerHost, type Wire } from '../../harness/fixtures';

function graph(repos: Repository[], pollTimeout = 600) {
  const prompt = [
    'import os, pathlib, subprocess',
    `for name in ${JSON.stringify(repos.map((repo) => repo.name))}:`,
    "    path = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / name",
    "    subprocess.run(['git', '-C', str(path), 'switch', '-C', 'feature/park'], check=True)",
    "    (path / 'PARK.md').write_text('held on a flow\\n')",
    "    subprocess.run(['git', '-C', str(path), 'add', '-A'], check=True)",
    "    subprocess.run(['git', '-C', str(path), '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: park'], check=True)",
    "    sha = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()",
    "    subprocess.run(['git', '-C', str(path), 'push', 'origin', 'feature/park'], check=True)",
    "    subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name, '--branch', 'feature/park', '--commit', sha], check=True)",
  ].join('\n');
  return JSON.stringify({ name: 'default-delivery', entry: 'build', nodes: {
    build: { executor: 'runner', prompt, judgement: { prompt: "verdict('pass', 'built')\n", choices: { pass: { description: 'Built.', to: 'deliver' } } }, retries: { max: 1, exhausted: 'escalate' } },
    deliver: { executor: 'hub', action: 'land', poll_interval: 1, poll_timeout: pollTimeout, judgement: { choices: {
      landed: { description: 'Landed.', to: 'done' }, conflict: { description: 'Conflict.', to: 'build' },
      failure: { description: 'Failure.', to: 'build' }, 'inherited-failure': { description: 'Base failure.', to: 'build' },
    } } },
  } });
}

type Pull = { number: number };
async function pulls(forge: ForgeApi, repo: Repository, state = 'open'): Promise<Pull[]> {
  const response = await forge.call('GET', `/repos/${repo.owner}/${repo.name}/pulls?state=${state}`);
  expect(response.status).toBe(200);
  return response.body as Pull[];
}

async function read(api: HubApi, id: string): Promise<Wire.ChunkDetail> {
  const response = await api.get<Wire.ChunkDetail>(`/api/chunks/${id}`);
  expect(response.status).toBe(200);
  return response.body;
}

type LandingFact = { sequence: number; repo: string; commit_hash: string; landed_at: string };
async function landingFacts(api: HubApi, id: string): Promise<LandingFact[]> {
  const facts: LandingFact[] = [];
  let cursor: number | null = 0;
  while (cursor !== null) {
    const response: { status: number; body: { items: LandingFact[]; next_cursor: number | null } } =
      await api.get(`/api/chunks/${id}/landing-facts?limit=1&cursor=${cursor}`);
    expect(response.status).toBe(200);
    facts.push(...response.body.items);
    cursor = response.body.next_cursor;
  }
  return facts;
}

type Submission = { submission_id: string; terms: { landing_policy: string }[]; closure: { outcome: string } | null };
type Turn = { turn_id: string; ended_at: string | null };
async function submissions(api: HubApi, id: string): Promise<Submission[]> {
  const response = await api.get<{ items: Submission[] }>(`/api/chunks/${id}/flow-submissions`);
  expect(response.status).toBe(200);
  return response.body.items;
}

async function turns(api: HubApi, id: string): Promise<Turn[]> {
  const result: Turn[] = [];
  for (const submission of await submissions(api, id)) {
    let before: string | null = null;
    do {
      const query: string = before === null ? '' : `?before=${encodeURIComponent(before)}`;
      const response: { status: number; body: { items: Turn[]; next_before: string | null } } =
        await api.get(`/api/chunks/${id}/flow-submissions/${submission.submission_id}/turns${query}`);
      expect(response.status).toBe(200);
      result.push(...response.body.items);
      before = response.body.next_before;
    } while (before !== null);
  }
  return result;
}

const fullName = (repo: Repository) => `${repo.owner}/${repo.name}`;

async function merge(forge: ForgeApi, repo: Repository, number: number, method: 'merge' | 'squash') {
  const response = await forge.call('PUT', `/repos/${fullName(repo)}/pulls/${number}/merge`, { merge_method: method });
  expect(response.status, JSON.stringify(response.body)).toBe(200);
  return response.body.sha as string;
}

async function order(api: HubApi, project: string, first: Repository, second: Repository) {
  const response = await api.put(`/api/projects/${project}/landing-order`, { data: { repositories: [first.name, second.name] } });
  expect(response.status, JSON.stringify(response.body)).toBe(200);
}

function marker(detail: Wire.ChunkDetail, repo: Repository) {
  const matches = detail.artifacts.filter((artifact) => artifact.name.startsWith(`delivery-pr/${fullName(repo)}/`));
  expect(matches).toHaveLength(1);
  expect(matches[0]!.produced_at).not.toBeNull();
  const producedAt = Date.parse(matches[0]!.produced_at!);
  expect(Number.isFinite(producedAt)).toBe(true);
  return producedAt;
}


type Clock = Parameters<Parameters<typeof test>[2]>[0]['clock'];

// Every park case runs on a controlled hub, which starts no flow-driver timer: each wait is a bounded loop of the runner
// host and one flow turn. A turn moves the stack's clock only to the pending visit's own `next_poll_at`, when that
// lies ahead, so a parked visit is polled at its `PARK_POLL_FLOOR` cadence and the clock never overshoots a timeout.
async function turn(api: HubApi, clock: Pick<Clock, 'now' | 'set'>, id: string): Promise<Wire.ChunkDetail> {
  const { pending } = await read(api, id);
  const due = pending?.next_poll_at ? Date.parse(pending.next_poll_at) : NaN;
  if (due > clock.now().getTime()) await clock.set(new Date(due));
  await api.flowStep();
  return read(api, id);
}

const chunkOn = (host: RunnerHost, api: HubApi, clock: Pick<Clock, 'now' | 'set'>, id: string, reached: (detail: Wire.ChunkDetail) => boolean) =>
  stepUntil(host, () => turn(api, clock, id), reached, { stallPasses: 80, progress: chunkProgress });

async function landedOnClock(host: RunnerHost, api: HubApi, clock: Pick<Clock, 'now' | 'set'>, id: string) {
  await stepUntil(host, async () => {
    await turn(api, clock, id);
    const landed = (await submissions(api, id)).some((item) => item.closure?.outcome === 'landed');
    const history = await turns(api, id);
    return { landed, ended: history.length > 0 && history.every((item) => item.ended_at !== null) };
  }, (state) => state.landed && state.ended, { stallPasses: 80 });
}

test.describe('a parked delivery, on the controlled clock', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled' });
  for (const method of ['merge', 'squash', 'rebase'] as const) {
    test(`test_park_waits_past_node_timeout_then_records_human_merge[${method}]`, async ({ repository, issue, api, clock, forge, runnerHosts }) => {
      const repo = await repository();
      const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
      expect((await api.patch(`/api/repositories/${repo.name}`, { data: { landing_policy: 'park' } })).status).toBe(200);
      expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo], 2) } })).status).toBe(201);
      const { chunk } = await issue('park', { repository: repo });
      const id = chunk.chunk_id;
      expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
      const initial = await chunkOn(host, api, clock, id, (detail) => detail.pending?.kind === 'awaiting-merge');
      expect(Date.parse(initial.pending!.next_poll_at) - Date.parse(initial.pending!.since!)).toBeGreaterThanOrEqual(119_000);
      const opened = await pulls(forge, repo);
      expect(opened).toHaveLength(1);
      expect((await api.patch(`/api/repositories/${repo.name}`, { data: { landing_policy: 'merge-on-checks' } })).status).toBe(200);
      expect((await submissions(api, id)).flatMap((submission) => submission.terms).map((term) => term.landing_policy)).toEqual(['park']);
      const held = await chunkOn(host, api, clock, id, (detail) => detail.status !== 'delivering' || detail.pending?.next_poll_at !== initial.pending!.next_poll_at);
      expect(held.status).toBe('delivering');
      expect(held.pending?.kind).toBe('awaiting-merge');
      const merged = await forge.call('PUT', `/repos/${repo.owner}/${repo.name}/pulls/${opened[0]!.number}/merge`, { merge_method: method });
      expect(merged.status).toBe(200);
      const done = await chunkOn(host, api, clock, id, (detail) => ['done', 'needs_human', 'stopped'].includes(detail.status));
      expect([done.status, done.landed]).toEqual(['done', true]);
      expect(done.landed_repos).toHaveLength(1);
      expect(done.landed_repos[0]!.commit_hash).toBe(merged.body.sha);
      expect(done.history.at(-1)).toEqual(expect.objectContaining({ from_node_name: 'deliver', choice_name: 'landed' }));
      await landedOnClock(host, api, clock, id);
    });
  }

  for (const requeue of [false, true]) {
    test(`test_closed_parked_change_escalates_then_requeue_opens_fresh_change[${requeue ? 'closed-requeue' : 'closed'}]`, async ({ repository, issue, api, clock, forge, runnerHosts }) => {
      const repo = await repository();
      const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
      expect((await api.patch(`/api/repositories/${repo.name}`, { data: { landing_policy: 'park' } })).status).toBe(200);
      expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo]) } })).status).toBe(201);
      const { chunk } = await issue('closed park', { repository: repo });
      const id = chunk.chunk_id;
      expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
      await chunkOn(host, api, clock, id, (detail) => detail.pending?.kind === 'awaiting-merge');
      const opened = await pulls(forge, repo);
      expect(opened).toHaveLength(1);
      expect((await forge.call('PATCH', `/repos/${repo.owner}/${repo.name}/pulls/${opened[0]!.number}`, { state: 'closed' })).status).toBe(200);
      const escalated = await chunkOn(host, api, clock, id, (detail) => detail.status === 'needs_human');
      expect(escalated.escalation?.cause).toBe('change-closed');
      expect(escalated.pending).toBeNull();
      if (requeue) {
        const previousTurns = new Set((await turns(api, id)).map((turn) => turn.turn_id));
        // The person requeues at an instant of their own, not the escalation's.
        await clock.advance(1);
        expect((await api.post(`/api/chunks/${id}/requeues`)).status).toBe(202);
        // The requeued visit is polled at the node's one-second interval, so each pass also moves the clock a second.
        await stepUntil(host, async () => { await clock.advance(1); return { detail: await turn(api, clock, id), rows: await pulls(forge, repo) }; }, (state) => state.rows.length === 1,
          { stallPasses: 80, progress: (state) => [chunkProgress(state.detail), state.rows] });
        await stepUntil(host, async () => { await clock.advance(1); await turn(api, clock, id); return turns(api, id); },
          (history) => history.some((item) => !previousTurns.has(item.turn_id) && item.ended_at !== null), { stallPasses: 80 });
        const replacement = await pulls(forge, repo);
        expect(replacement).toHaveLength(1);
        expect(replacement[0]!.number).not.toBe(opened[0]!.number);
      }
    });
  }

  for (const parkedFirst of [true, false]) {
    test(`test_parked_repository_holds_landing_order_until_person_merges[${parkedFirst ? 'library-parked' : 'consumer-parked'}]`, async ({ repository, issue, project, api, clock, forge, runnerHosts }) => {
      const consumer = await repository();
      const library = await repository();
      const host = await runnerHosts.create({ runnerRepositories: { [consumer.name]: consumer.origin!, [library.name]: library.origin! } });
      await order(api, project.slug, library, consumer);
      const target = parkedFirst ? library : consumer;
      expect((await api.patch(`/api/repositories/${target.name}`, { data: { landing_policy: 'park' } })).status).toBe(200);
      expect((await api.post('/api/graphs', { data: { definition_yaml: graph([consumer, library]) } })).status).toBe(201);
      const { chunk } = await issue('ordered park', { repository: consumer });
      const id = chunk.chunk_id;
      expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
      const held = await chunkOn(host, api, clock, id, (detail) => detail.pending?.kind === 'awaiting-merge');
      expect(held.landed_repos.map((row) => row.repo)).toEqual(parkedFirst ? [] : [fullName(library)]);
      expect((await landingFacts(api, id)).map((fact) => fact.repo)).toEqual(parkedFirst ? [] : [fullName(library)]);
      const opened = await pulls(forge, target);
      expect(opened).toHaveLength(1);
      expect((await pulls(forge, parkedFirst ? consumer : library)).length).toBeLessThanOrEqual(1);
      const sha = await merge(forge, target, opened[0]!.number, 'merge');
      const done = await chunkOn(host, api, clock, id, (detail) => ['done', 'needs_human', 'stopped'].includes(detail.status));
      expect([done.status, done.landed]).toEqual(['done', true]);
      expect(done.landed_repos).toHaveLength(2);
      expect(done.landed_repos.find((row) => row.repo === fullName(target))?.commit_hash).toBe(sha);
      const facts = await landingFacts(api, id);
      expect(facts.map((fact) => fact.repo)).toEqual([fullName(library), fullName(consumer)]);
      expect(facts.find((fact) => fact.repo === fullName(target))?.commit_hash).toBe(sha);
      expect(facts[1]!.sequence).toBeGreaterThan(facts[0]!.sequence);
      await landedOnClock(host, api, clock, id);
    });
  }


  test('test_second_parked_repository_has_its_own_review_clock', async ({ repository, issue, project, api, clock, forge, runnerHosts }) => {
    const consumer = await repository();
    const library = await repository();
    const host = await runnerHosts.create({ runnerRepositories: { [consumer.name]: consumer.origin!, [library.name]: library.origin! } });
    await order(api, project.slug, library, consumer);
    for (const [repo, hours] of [[library, 2], [consumer, 1]] as const) {
      expect((await api.patch(`/api/repositories/${repo.name}`, { data: { landing_policy: 'park', park_timeout_hours: hours } })).status).toBe(200);
    }
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph([consumer, library]) } })).status).toBe(201);
    const { chunk } = await issue('independent park clocks', { repository: consumer });
    const id = chunk.chunk_id;
    expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
    const firstHeld = await chunkOn(host, api, clock, id, (detail) => detail.pending?.kind === 'awaiting-merge');
    const firstAt = marker(firstHeld, library);
    const first = await pulls(forge, library);
    expect(first).toHaveLength(1);
    expect(await pulls(forge, consumer, 'all')).toEqual([]);
    // Past the consumer's hour, short of the library's two: the library's visit is still parked.
    await clock.set(new Date(Math.max(firstAt + 3_600_000 + 1000, clock.now().getTime())));
    await api.flowStep();
    const waiting = await read(api, id);
    expect(waiting.status).toBe('delivering');
    expect(waiting.pending?.kind).toBe('awaiting-merge');
    const firstSha = await merge(forge, library, first[0]!.number, 'merge');
    const secondHeld = await chunkOn(host, api, clock, id,
      (detail) => detail.pending?.kind === 'awaiting-merge' && detail.artifacts.some((artifact) => artifact.name.startsWith(`delivery-pr/${fullName(consumer)}/`)));
    expect((await pulls(forge, consumer)).length).toBe(1);
    const secondAt = marker(secondHeld, consumer);
    expect(secondAt - firstAt).toBeGreaterThan(3_600_000);
    expect(secondHeld.status).toBe('delivering');
    expect(secondHeld.pending?.kind).toBe('awaiting-merge');
    const evaluated = await turn(api, clock, id);
    expect(evaluated.status).toBe('delivering');
    expect(evaluated.pending?.kind).toBe('awaiting-merge');
    const second = await pulls(forge, consumer);
    expect(second).toHaveLength(1);
    const secondSha = await merge(forge, consumer, second[0]!.number, 'squash');
    const done = await chunkOn(host, api, clock, id, (detail) => ['done', 'needs_human', 'stopped'].includes(detail.status));
    expect([done.status, done.landed]).toEqual(['done', true]);
    expect(Object.fromEntries(done.landed_repos.map((row) => [row.repo, row.commit_hash]))).toEqual({ [fullName(library)]: firstSha, [fullName(consumer)]: secondSha });
    expect((await landingFacts(api, id)).map(({ repo, commit_hash }) => [repo, commit_hash])).toEqual([
      [fullName(library), firstSha], [fullName(consumer), secondSha],
    ]);
    await landedOnClock(host, api, clock, id);
  });

  test('test_park_timeout_then_person_merge_is_adopted_on_requeue', async ({ repository, issue, api, clock, forge, runnerHosts }) => {
    const repo = await repository();
    const host = await runnerHosts.create({ runnerRepositories: { [repo.name]: repo.origin! } });
    expect((await api.patch(`/api/repositories/${repo.name}`, { data: { landing_policy: 'park', park_timeout_hours: 1 } })).status).toBe(200);
    expect((await api.post('/api/graphs', { data: { definition_yaml: graph([repo]) } })).status).toBe(201);
    const { chunk } = await issue('park timeout', { repository: repo });
    const id = chunk.chunk_id;
    expect((await api.post(`/api/chunks/${id}/promote`)).status).toBe(202);
    const held = await chunkOn(host, api, clock, id, (detail) => detail.pending?.kind === 'awaiting-merge');
    expect(held.pending!.since).not.toBeNull();
    const producedAt = marker(held, repo);
    const opened = await pulls(forge, repo);
    expect(opened).toHaveLength(1);
    await clock.set(new Date(Math.max(producedAt + 3_600_000, clock.now().getTime())));
    const expired = await chunkOn(host, api, clock, id, (detail) => detail.status === 'needs_human');
    expect(expired.escalation?.cause).toBe('park-timeout');
    expect(expired.pending).toBeNull();
    expect(clock.now().getTime() - producedAt).toBeGreaterThanOrEqual(3_600_000);
    // The person merges and requeues after the escalation, at an instant of their own, not the escalation's.
    await clock.advance(1);
    const sha = await merge(forge, repo, opened[0]!.number, 'squash');
    expect((await api.post(`/api/chunks/${id}/requeues`)).status).toBe(202);
    const done = await chunkOn(host, api, clock, id, (detail) => detail.status === 'done');
    expect([done.status, done.landed]).toEqual(['done', true]);
    expect(done.landed_repos).toHaveLength(1);
    expect(done.landed_repos[0]!.commit_hash).toBe(sha);
    expect(done.pending).toBeNull();
    expect(await pulls(forge, repo, 'all')).toHaveLength(1);
    await landedOnClock(host, api, clock, id);
  });
});
