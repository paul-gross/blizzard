import { expect, runnerTest as test, stepUntil, stepUntilHeld } from '../../harness/fixtures';
import type { MockChunkSpec, MockHub, RunnerHost } from '../../harness/fixtures';

// A utilization line holds a runner's next node and not the step in flight: one host, one runner spending a sampled
// subscription and configuring a line. A sample over the line stands while the chunk's first node runs; that step
// finishes, its next node is declined — no lease minted, no retry spent — and the hub shows the runner declining on
// `utilization`; a sample under the line lets the next node enter. Samples come from the vendor counterpart: set what
// the credential reports, step the host, and the runner reads it.

const RUNNER = 'runner-local';
const CHECKPOINT = 'build';
const MAX_HOST_STEPS = 20_000;

/** What a credential reports when both windows sit at `pct`: the line names no window, so either one over is over. */
function sample(pct: number) {
  const now = Math.floor(Date.now() / 1000);
  return { short: { utilization: pct, resetsAt: now + 3 * 3_600 }, long: { utilization: pct, resetsAt: now + 48 * 3_600 } };
}

/** A build → review → done chunk whose build worker is held at the checkpoint until released. */
function twoNodes(hold: string): MockChunkSpec {
  const node = (prompt: string, to: string) => ({
    executor: 'runner' as const,
    session: 'resume' as const,
    judged_by: 'worker' as const,
    prompt,
    judgement_prompt: "verdict('pass', 'the mock harness finished; checks are green')\n",
    choices: [{ name: 'pass', description: 'done', to }],
    retries_max: 1,
  });
  return {
    entry: 'build',
    nodes: { build: node(hold, 'review'), review: node('pass\n', 'done') },
    work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
  };
}

async function leasesOn(host: RunnerHost, chunkId: string, node: string) {
  return (await host.runners[RUNNER]!.leases()).body.items.filter((lease) => lease.chunk_id === chunkId && lease.node_name === node);
}

/** The levers the hub holds the runner declining on, as its own read of itself serves them. */
async function levers(mockHub: MockHub, host: RunnerHost): Promise<string[]> {
  const view = await mockHub.fleetOf(host, RUNNER).get(`/api/fleet/runners/${host.runners[RUNNER]!.id}`);
  expect(view.status).toBe(200);
  return (view.body as { declining: { lever: string }[] }).declining.map((reason) => reason.lever);
}

async function chunkNode(mockHub: MockHub, host: RunnerHost, chunkId: string): Promise<string> {
  const detail = await mockHub.fleetOf(host, RUNNER).get(`/api/fleet/chunks/${chunkId}`);
  expect(detail.status).toBe(200);
  return (detail.body as { current_node_id: string }).current_node_id;
}

test('test_a_utilization_line_declines_the_next_node_and_never_the_step_in_flight', async ({ mockHub, runnerHosts, vendorUsage, workerCheckpoint }, testInfo) => {
  const credential = vendorUsage.mintAnthropic(testInfo.outputPath('vendor'));
  vendorUsage.set(credential.token, sample(40));
  const host = await runnerHosts.create({
    runnerConfig: {
      subscription: [{ slug: 'anthropic', credentials_path: credential.path, usage_api_base: '${usageBase}', sample_interval_seconds: 1n }],
      runner: [{ name: RUNNER, subscription: 'anthropic', throttle: { utilization: { below_pct: 95 } } }],
    },
  });
  // The line reads a sample only while it is fresh, so the subscription keeps a short real interval: a resample is due
  // once it has passed, and the host is stepped until the recorded sample is the one the counterpart now reports.
  const sampled = async (pct: number): Promise<void> => {
    vendorUsage.set(credential.token, sample(pct));
    for (let pass = 0; ; pass++) {
      if (pass === MAX_HOST_STEPS) throw new Error(`no sample at ${pct}% within ${MAX_HOST_STEPS} host passes\n${host.logTail()}`);
      expect((await host.stepHost()).outcome).toBe('completed');
      const item = (await host.api.subscriptions()).body.items.find((entry) => entry.slug === 'anthropic');
      if (item?.ok && item.windows.length === 2 && item.windows.every((window) => window.utilization_pct === pct)) return;
    }
  };

  await sampled(40);
  const chunkId = (await mockHub.seed.chunk(twoNodes(workerCheckpoint.snippet(CHECKPOINT)))).body.chunk_id;
  await stepUntilHeld(host, chunkId, workerCheckpoint, CHECKPOINT);
  const [building] = await leasesOn(host, chunkId, 'build');
  expect(building!.closed_at ?? null).toBeNull();

  // The line is crossed while the first step runs: the hub shows the runner declining, and the step is untouched.
  await sampled(96);
  await stepUntil(host, () => levers(mockHub, host), (declining) => declining.length > 0);
  expect(await levers(mockHub, host)).toEqual(['utilization']);
  expect((await leasesOn(host, chunkId, 'build'))[0]!.closed_at ?? null).toBeNull();

  // The step finishes, and its next node is declined: a pass completes after the build lease closed, with the chunk
  // waiting at review and no lease for it.
  workerCheckpoint.release(CHECKPOINT);
  await stepUntil(host, async () => (await leasesOn(host, chunkId, 'build'))[0]!.closed_at ?? null, (closedAt) => closedAt !== null);
  expect((await host.step()).outcome).toBe('completed');
  expect(await chunkNode(mockHub, host, chunkId)).toBe('review');
  expect(await leasesOn(host, chunkId, 'review')).toEqual([]);
  expect(await leasesOn(host, chunkId, 'build')).toHaveLength(1);
  expect(await levers(mockHub, host)).toEqual(['utilization']);

  // A sample under the line: the next node enters, and the hub shows nothing declining.
  await sampled(40);
  await stepUntil(host, async () => (await leasesOn(host, chunkId, 'review')).length, (count) => count === 1);
  await stepUntil(host, () => levers(mockHub, host), (declining) => declining.length === 0);
  expect(await leasesOn(host, chunkId, 'build')).toHaveLength(1);
});
