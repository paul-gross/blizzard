import { expect, runnerTest as test } from '../../harness/fixtures';
import type { MockChunkSpec, MockHub, RunnerHost, WorkerCheckpoint } from '../../harness/fixtures';

const FIRST = 'runner-local';
const NEIGHBOUR = 'runner-b';
const NAMES = [FIRST, NEIGHBOUR];
const LINE = 95;
const MAX_PASSES = 600;

test.setTimeout(240_000);

function sample(pct: number) {
  const now = Math.floor(Date.now() / 1000);
  return { short: { utilization: pct, resetsAt: now + 3 * 3600 }, long: { utilization: pct, resetsAt: now + 48 * 3600 } };
}

function minuteAhead(hours: number): number {
  return Math.floor((Date.now() / 1000 + hours * 3600) / 60) * 60;
}

function named(resetsAt: number): string {
  const at = new Date(resetsAt * 1000);
  const hour = at.getUTCHours();
  return `${hour % 12 || 12}:${String(at.getUTCMinutes()).padStart(2, '0')}${hour < 12 ? 'am' : 'pm'} (UTC)`;
}

function node(prompt: string, to: string): MockChunkSpec['nodes'][string] {
  return {
    executor: 'runner', session: 'resume', judged_by: 'worker', prompt,
    judgement_prompt: "verdict('pass', 'the mock harness finished; checks are green')\n",
    choices: [{ name: 'pass', description: 'done', to }], retries_max: 1,
  };
}

function twoNodes(checkpoint: WorkerCheckpoint): MockChunkSpec {
  return {
    entry: 'build', nodes: { build: node(checkpoint.snippet('build'), 'review'), review: node(checkpoint.snippet('review'), 'done') },
    work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
  };
}

async function passes(host: RunnerHost): Promise<void> {
  for (const name of NAMES) expect((await host.step(name)).outcome).toBe('completed');
}

async function until<T>(host: RunnerHost, read: () => Promise<T>, reached: (value: T) => boolean): Promise<T> {
  let value = await read();
  for (let count = 0; !reached(value); count++) {
    if (count === MAX_PASSES) throw new Error(`not reached after ${MAX_PASSES} passes: ${JSON.stringify(value)}\n${host.logTail()}`);
    await passes(host);
    value = await read();
  }
  return value;
}

async function leases(host: RunnerHost, name: string, chunkId: string) {
  return (await host.runners[name]!.leases()).body.items.filter((lease) => lease.chunk_id === chunkId);
}

async function held(host: RunnerHost, chunkId: string) {
  return (await host.api.environments()).body.workspaces.flatMap((workspace) => workspace.items)
    .filter((environment) => environment.held && environment.chunk_id === chunkId);
}

async function detail(mockHub: MockHub, host: RunnerHost, chunkId: string) {
  const response = await mockHub.fleetOf(host, FIRST).get(`/api/fleet/chunks/${chunkId}`);
  expect(response.status).toBe(200);
  return response.body as { status: string; current_node_id: string };
}

async function declining(mockHub: MockHub, host: RunnerHost, name: string): Promise<string[]> {
  const response = await mockHub.fleetOf(host, name).get(`/api/fleet/runners/${host.runners[name]!.id}`);
  expect(response.status).toBe(200);
  return (response.body as { declining: { lever: string }[] }).declining.map((reason) => reason.lever);
}

async function sampled(host: RunnerHost, slug: string, pct: number): Promise<void> {
  for (let count = 0; count < MAX_PASSES; count++) {
    expect((await host.stepHost()).outcome).toBe('completed');
    const subscription = (await host.api.subscriptions()).body.items.find((item) => item.slug === slug);
    if (subscription?.ok && subscription.windows.length === 2 && subscription.windows.every((window) => window.utilization_pct === pct)) return;
  }
  throw new Error(`no ${slug} sample at ${pct}%\n${host.logTail()}`);
}

async function setup(runnerHosts: { create: (options: { runnerConfig: Record<string, unknown> }) => Promise<RunnerHost> },
  firstPath: string, secondPath: string, usageLimit = false): Promise<RunnerHost> {
  const bind = (name: string, subscription: string) => ({
    name, subscription, agents: 2n,
    throttle: { utilization: { below_pct: LINE }, on_decline: 'release',
      ...(name === FIRST && usageLimit ? { on_usage_limit: 'release' } : {}) },
  });
  return runnerHosts.create({
    runnerConfig: {
      workspace: [{ id: 'workspace-local', envs: ['e1', 'e2'], max_environments: 2n }],
      subscription: [
        { slug: 'anthropic', credentials_path: firstPath, usage_api_base: '${usageBase}', sample_interval_seconds: 1n },
        { slug: 'second-plan', name: 'Second plan', provider: 'anthropic', credentials_path: secondPath, usage_api_base: '${usageBase}', sample_interval_seconds: 1n },
      ],
      runner: [bind(FIRST, 'anthropic'), bind(NEIGHBOUR, 'second-plan')],
    },
  });
}

async function releasedGate(mockHub: MockHub, host: RunnerHost, checkpoint: WorkerCheckpoint, chunkId: string) {
  let arrived = false;
  void checkpoint.reached('build').then(() => (arrived = true));
  const [original] = await until(host, () => leases(host, FIRST, chunkId), (found) => found.length === 1 && arrived);
  expect(original!.state).toBe('running');
  expect(await held(host, chunkId)).toMatchObject([{ runner_id: host.runners[FIRST]!.id, chunk_id: chunkId }]);
  checkpoint.release('build');
  await until(host, async () => ({ lease: (await leases(host, FIRST, chunkId))[0], chunk: await detail(mockHub, host, chunkId) }),
    ({ lease, chunk }) => lease?.closed_at !== null && chunk.status === 'ready' && chunk.current_node_id === 'review');
  expect(await leases(host, FIRST, chunkId)).toHaveLength(1);
  expect((await leases(host, FIRST, chunkId))[0]!.closure_reason).toBe('transitioned');
  expect(await held(host, chunkId)).toEqual([]);
  return original!;
}

async function refuseBoth(mockHub: MockHub, host: RunnerHost, chunkId: string): Promise<void> {
  await until(host, async () => Promise.all(NAMES.map((name) => declining(mockHub, host, name))),
    (reasons) => reasons.every((reason) => reason.includes('utilization')));
  for (const name of NAMES) {
    const status = (await host.runners[name]!.status()).body;
    expect(status.throttle?.declining).toContain('utilization');
    expect(status.throttle?.state).toBe('declining');
    expect((await host.step(name)).outcome).toBe('completed');
  }
  expect(await detail(mockHub, host, chunkId)).toMatchObject({ status: 'ready', current_node_id: 'review' });
  expect(await leases(host, FIRST, chunkId)).toHaveLength(1);
  expect(await leases(host, NEIGHBOUR, chunkId)).toEqual([]);
  expect(await held(host, chunkId)).toEqual([]);
  for (const name of NAMES) expect(await declining(mockHub, host, name)).toContain('utilization');
}

test('test_a_declined_next_step_is_released_to_a_neighbour_and_waits_ready_while_every_gate_declines', async ({ mockHub, runnerHosts, vendorUsage, workerCheckpoint }, testInfo) => {
  const first = vendorUsage.mintAnthropic(testInfo.outputPath('vendor-first'));
  const second = vendorUsage.mintAnthropic(testInfo.outputPath('vendor-second'));
  vendorUsage.set(first.token, sample(40));
  vendorUsage.set(second.token, sample(96));
  const host = await setup(runnerHosts, first.path, second.path);
  expect((await host.api.runners()).body.items.map((runner) => runner.runner_id)).toEqual(expect.arrayContaining(NAMES.map((name) => host.runners[name]!.id)));
  await sampled(host, 'anthropic', 40);
  await sampled(host, 'second-plan', 96);
  const chunkId = (await mockHub.seed.chunk(twoNodes(workerCheckpoint))).body.chunk_id;
  let arrived = false;
  void workerCheckpoint.reached('build').then(() => (arrived = true));
  await until(host, () => leases(host, FIRST, chunkId), (found) => found.length === 1 && arrived);
  vendorUsage.set(first.token, sample(96));
  await sampled(host, 'anthropic', 96);
  const original = await releasedGate(mockHub, host, workerCheckpoint, chunkId);
  await refuseBoth(mockHub, host, chunkId);
  vendorUsage.set(second.token, sample(40));
  await sampled(host, 'second-plan', 40);
  const [next] = await until(host, () => leases(host, NEIGHBOUR, chunkId), (found) => found.length === 1);
  expect(next!.lease_id).not.toBe(original.lease_id);
  expect(next!.epoch).toBeGreaterThan(original.epoch);
  expect(next!.node_name).toBe('build');
  expect(await leases(host, FIRST, chunkId)).toHaveLength(1);
});

test('test_a_released_chunk_is_claimed_cold_by_its_first_runner_once_its_gate_reopens', async ({ mockHub, runnerHosts, vendorUsage, workerCheckpoint }, testInfo) => {
  const first = vendorUsage.mintAnthropic(testInfo.outputPath('vendor-first'));
  const second = vendorUsage.mintAnthropic(testInfo.outputPath('vendor-second'));
  vendorUsage.set(first.token, sample(40));
  vendorUsage.set(second.token, sample(96));
  const host = await setup(runnerHosts, first.path, second.path);
  await sampled(host, 'anthropic', 40);
  await sampled(host, 'second-plan', 96);
  const chunkId = (await mockHub.seed.chunk(twoNodes(workerCheckpoint))).body.chunk_id;
  let arrived = false;
  void workerCheckpoint.reached('build').then(() => (arrived = true));
  await until(host, () => leases(host, FIRST, chunkId), (found) => found.length === 1 && arrived);
  vendorUsage.set(first.token, sample(96));
  await sampled(host, 'anthropic', 96);
  const original = await releasedGate(mockHub, host, workerCheckpoint, chunkId);
  await refuseBoth(mockHub, host, chunkId);
  vendorUsage.set(first.token, sample(40));
  await sampled(host, 'anthropic', 40);
  const next = await until(host, () => leases(host, FIRST, chunkId), (found) => found.length === 2);
  const reclaimed = next.find((lease) => lease.lease_id !== original.lease_id)!;
  expect(reclaimed.epoch).toBeGreaterThan(original.epoch);
  expect(reclaimed.node_name).toBe('build');
  expect(await leases(host, NEIGHBOUR, chunkId)).toEqual([]);
});

test('test_a_usage_limited_lease_on_a_runner_set_to_release_is_claimed_elsewhere_from_its_step_s_start', async ({ mockHub, runnerHosts, vendorUsage }, testInfo) => {
  const first = vendorUsage.mintAnthropic(testInfo.outputPath('vendor-first'));
  const second = vendorUsage.mintAnthropic(testInfo.outputPath('vendor-second'));
  const reset = minuteAhead(2);
  vendorUsage.set(first.token, { short: { utilization: 90, resetsAt: reset }, long: sample(90).long });
  vendorUsage.set(second.token, sample(96));
  const host = await setup(runnerHosts, first.path, second.path, true);
  await sampled(host, 'anthropic', 90);
  await sampled(host, 'second-plan', 96);
  const spec: MockChunkSpec = { entry: 'build', nodes: { build: node(`usage_limited(resets_at="${named(reset)}")\n`, 'review'), review: node('pass\n', 'done') }, work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }] };
  const chunkId = (await mockHub.seed.chunk(spec)).body.chunk_id;
  const [original] = await until(host, async () => leases(host, FIRST, chunkId),
    (found) => found.length === 1 && found[0]!.closure_reason === 'released');
  expect(original!.state).toBe('closed');
  expect(original!.node_name).toBe('build');
  expect(await detail(mockHub, host, chunkId)).toMatchObject({ status: 'ready', current_node_id: 'build' });
  const usage = (await host.runners[FIRST]!.leaseUsage(original!.lease_id)).body.items.filter((item) => item.kind === 'spawn' || item.kind === 'resume');
  expect(usage).toHaveLength(1);
  expect(usage[0]!.input_tokens + usage[0]!.output_tokens).toBeGreaterThan(0);
  expect(usage[0]!.cost_usd).toBeGreaterThan(0);
  expect((await host.runners[FIRST]!.status()).body.throttle).toMatchObject({ on_usage_limit: 'release', floor: { hold: { kind: 'mark', lease_id: original!.lease_id } } });
  expect((await leases(host, FIRST, chunkId)).every((lease) => lease.state === 'closed')).toBe(true);
  expect(await held(host, chunkId)).toEqual([]);
  await refuseBothUsage(mockHub, host, chunkId);
  vendorUsage.set(second.token, sample(40));
  await sampled(host, 'second-plan', 40);
  const [next] = await until(host, () => leases(host, NEIGHBOUR, chunkId), (found) => found.length === 1);
  expect(next!.lease_id).not.toBe(original!.lease_id);
  expect(next!.epoch).toBeGreaterThan(original!.epoch);
  expect(next!.node_name).toBe('build');
  expect(next!.session_id).not.toBe(original!.session_id);
  expect(next!.session_id).not.toBeNull();
  expect(await leases(host, FIRST, chunkId)).toHaveLength(1);
});

async function refuseBothUsage(mockHub: MockHub, host: RunnerHost, chunkId: string): Promise<void> {
  await until(host, async () => Promise.all(NAMES.map((name) => declining(mockHub, host, name))),
    ([first, second]) => first!.includes('floor') && second!.includes('utilization'));
  expect((await host.runners[FIRST]!.status()).body.throttle?.declining).toContain('floor');
  expect((await host.runners[NEIGHBOUR]!.status()).body.throttle?.declining).toContain('utilization');
  for (const name of NAMES) expect((await host.step(name)).outcome).toBe('completed');
  expect(await detail(mockHub, host, chunkId)).toMatchObject({ status: 'ready', current_node_id: 'build' });
  expect(await leases(host, NEIGHBOUR, chunkId)).toEqual([]);
  expect(await leases(host, FIRST, chunkId)).toHaveLength(1);
  expect(await held(host, chunkId)).toEqual([]);
}
