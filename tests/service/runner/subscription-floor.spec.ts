import { expect, runnerTest as test } from '../../harness/fixtures';
import type { MockChunkSpec, MockHub, RunnerHost } from '../../harness/fixtures';

// A usage limit holds every runner spending its subscription, and no other: one host, two subscriptions, two runners
// spending the first and one the second. No runner's own brake engages; a turned-over sample opens the floor unaided, and
// so does `clear-floor` with a sample still showing it shut; the hub shows the floor as each held runner's declining
// reason while it holds, and nothing once it opens. Samples come from the vendor counterpart: set what a credential
// reports, step the host, and read the recorded sample off the host's subscriptions.

const MAX_STEPS = 60;
const FIRST = 'anthropic';
const SECOND = 'second-plan';
const A = 'runner-local';
const B = 'runner-b';
const C = 'runner-c';
const RUNNERS = [A, B, C];
const ENVIRONMENTS = Array.from({ length: 24 }, (_, index) => `e${index + 1}`);

test.setTimeout(240_000);

/** A one-node chunk in `projectId` whose worker runs `prompt` and passes. */
function chunk(projectId: string, prompt = 'pass\n'): MockChunkSpec {
  return {
    project_id: projectId,
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        prompt,
        judgement_prompt: "verdict('pass', 'the mock harness finished; checks are green')\n",
        choices: [{ name: 'pass', description: 'done', to: 'done' }],
        retries_max: 1,
      },
    },
    work_refs: [{ source: 'mock', ref: `toy-api/issues/${Math.floor(Math.random() * 1e9)}` }],
  };
}

/** The reset a harness names for a usage limit, the way its worker prints one: `3:05pm (UTC)`. */
function named(resetsAt: number): string {
  const at = new Date(resetsAt * 1000);
  const hour = at.getUTCHours();
  const clock = `${hour % 12 === 0 ? 12 : hour % 12}:${String(at.getUTCMinutes()).padStart(2, '0')}${hour < 12 ? 'am' : 'pm'}`;
  return `${clock} (UTC)`;
}

/** An instant `hours` ahead, aligned to the minute, in epoch seconds. */
function minuteAhead(hours: number): number {
  return Math.floor((Date.now() / 1000 + hours * 3_600) / 60) * 60;
}

/** One pass of every runner, in order. */
async function pass(host: RunnerHost): Promise<void> {
  for (const name of RUNNERS) expect((await host.step(name)).outcome).toBe('completed');
}

/** Passes of every runner until `read` reaches a state `reached` accepts, failing after `MAX_STEPS` with the last state. */
async function passUntil<T>(host: RunnerHost, read: () => Promise<T>, reached: (state: T) => boolean): Promise<T> {
  let state = await read();
  for (let count = 0; !reached(state); count++) {
    if (count === MAX_STEPS) throw new Error(`not reached within ${MAX_STEPS} passes; last state ${JSON.stringify(state)}\n${host.logTail()}`);
    await pass(host);
    state = await read();
  }
  return state;
}

/** The leases `runner` holds on `chunkId`, through its own API. */
async function leasesOf(host: RunnerHost, runner: string, chunkId: string) {
  return (await host.runners[runner]!.leases()).body.items.filter((lease) => lease.chunk_id === chunkId);
}

async function hold(host: RunnerHost, runner: string) {
  return (await host.runners[runner]!.status()).body.throttle?.floor.hold ?? null;
}

/** The levers the hub holds `runner` declining on, as its own read of itself serves them. */
async function levers(mockHub: MockHub, host: RunnerHost, runner: string): Promise<string[]> {
  const view = await mockHub.fleetOf(host, runner).get(`/api/fleet/runners/${host.runners[runner]!.id}`);
  expect(view.status).toBe(200);
  return (view.body as { declining: { lever: string }[] }).declining.map((reason) => reason.lever);
}

/** Whether `lease`'s chunk has left its park, and each (runner, chunk) pair has leased. */
async function proceeded(host: RunnerHost, lease: { runner: string; chunk: string }, others: [string, string][]): Promise<boolean> {
  const [parked] = await leasesOf(host, lease.runner, lease.chunk);
  if (parked === undefined || parked.state === 'parked') return false;
  for (const [runner, chunk] of others) if ((await leasesOf(host, runner, chunk)).length === 0) return false;
  return true;
}

async function expectUnbraked(host: RunnerHost): Promise<void> {
  for (const name of RUNNERS) expect((await host.runners[name]!.status()).body.pause.local, name).toBe(false);
}

test('test_a_usage_limit_holds_its_subscription_s_runners_until_a_sample_or_a_clear_opens_it', async ({ mockHub, runnerHosts, vendorUsage }, testInfo) => {
  const dir = testInfo.outputPath('vendor');
  const first = vendorUsage.mintAnthropic(dir);
  const second = vendorUsage.mintAnthropic(dir);
  const reset = minuteAhead(2);
  const nextReset = reset + 5 * 3_600;
  const weekly = { utilization: 30, resetsAt: minuteAhead(48) };
  const quiet = { short: { utilization: 5, resetsAt: minuteAhead(3) }, long: { utilization: 5, resetsAt: minuteAhead(72) } };
  vendorUsage.set(second.token, quiet);
  vendorUsage.set(first.token, { short: { utilization: 90, resetsAt: reset }, long: weekly });

  const projects = Object.fromEntries(
    await Promise.all(['p-a', 'p-b', 'p-c'].map(async (slug) => [slug, (await mockHub.seed.project({ slug, name: slug })).body.project_id] as const)),
  );
  const bind = (name: string, subscription: string, project: string) => ({ name, subscription, agents: 6n, projects: [{ project, workspace: 'workspace-local' }] });
  const host = await runnerHosts.create({
    runnerConfig: {
      workspace: [{ id: 'workspace-local', envs: ENVIRONMENTS, max_environments: BigInt(ENVIRONMENTS.length) }],
      subscription: [
        { slug: FIRST, name: 'First plan', provider: 'anthropic', credentials_path: first.path, usage_api_base: '${usageBase}', sample_interval_seconds: 0n },
        { slug: SECOND, name: 'Second plan', provider: 'anthropic', credentials_path: second.path, usage_api_base: '${usageBase}', sample_interval_seconds: 0n },
      ],
      runner: [bind(A, FIRST, 'p-a'), bind(B, FIRST, 'p-b'), bind(C, SECOND, 'p-c')],
    },
  });

  // Before the limit: a recorded sample placing the window whose reset the harness will name.
  const sampledWindow = async (label: (windows: { window: string; resets_at: string }[]) => string | undefined): Promise<string> => {
    expect((await host.stepHost()).outcome).toBe('completed');
    const item = (await host.api.subscriptions()).body.items.find((entry) => entry.slug === FIRST);
    expect(item?.ok).toBe(true);
    const found = label(item?.windows ?? []);
    expect(found, 'the sample placed no window at the named reset').toBeDefined();
    return found!;
  };
  const atReset = (resetsAt: number) => (windows: { window: string; resets_at: string }[]) =>
    windows.find((window) => Date.parse(window.resets_at) / 1000 === resetsAt)?.window;
  const held = await sampledWindow(atReset(reset));

  const limited = (await mockHub.seed.chunk(chunk(projects['p-a']!, `usage_limited(resets_at="${named(reset)}")\n`))).body.chunk_id;
  const mark = await passUntil(host, () => hold(host, A), (standing) => standing !== null);
  const [limitedLease] = await leasesOf(host, A, limited);
  expect(mark).toMatchObject({ kind: 'mark', window: held, lease_id: limitedLease!.lease_id });
  expect(limitedLease!.state).toBe('parked');

  // The limit holds both of its subscription's runners, and not the third.
  const heldA = (await mockHub.seed.chunk(chunk(projects['p-a']!))).body.chunk_id;
  const heldB = (await mockHub.seed.chunk(chunk(projects['p-b']!))).body.chunk_id;
  const freeC = (await mockHub.seed.chunk(chunk(projects['p-c']!))).body.chunk_id;
  await passUntil(host, () => leasesOf(host, C, freeC), (leases) => leases.length > 0);
  // Every runner has been through the passes in which C leased its own chunk.
  expect(await leasesOf(host, A, heldA)).toEqual([]);
  expect(await leasesOf(host, B, heldB)).toEqual([]);
  expect((await leasesOf(host, A, limited))[0]!.state).toBe('parked');
  expect(await hold(host, B)).toMatchObject({ kind: 'mark', window: held, lease_id: limitedLease!.lease_id });
  await expectUnbraked(host);
  // The hub shows the floor as the reason for the two it holds, and nothing for the third.
  await passUntil(host, async () => [await levers(mockHub, host, A), await levers(mockHub, host, B)], (both) => both.every((seen) => seen.join() === 'floor'));
  expect(await levers(mockHub, host, C)).toEqual([]);

  // The window turned over, under 100 percent: the floor opens before its reset, with nobody acting.
  vendorUsage.set(first.token, { short: { utilization: 20, resetsAt: nextReset }, long: weekly });
  await sampledWindow(atReset(nextReset));
  const freeC2 = (await mockHub.seed.chunk(chunk(projects['p-c']!))).body.chunk_id;
  await passUntil(host, () => proceeded(host, { runner: A, chunk: limited }, [[A, heldA], [B, heldB], [C, freeC2]]), Boolean);
  await expectUnbraked(host);
  expect(await hold(host, A)).toBeNull();
  expect((await host.runners[A]!.status()).body.throttle?.floor.admits).toBe(true);
  await passUntil(host, () => levers(mockHub, host, A), (seen) => seen.length === 0);

  // A second limit, on the other spender, in the new period.
  const limitedAgain = (await mockHub.seed.chunk(chunk(projects['p-b']!, `usage_limited(resets_at="${named(nextReset)}")\n`))).body.chunk_id;
  const remark = await passUntil(host, () => hold(host, B), (standing) => standing !== null);
  const [relimitedLease] = await leasesOf(host, B, limitedAgain);
  expect(relimitedLease!.lease_id).not.toBe(limitedLease!.lease_id);
  expect(relimitedLease!.state).toBe('parked');
  expect(remark).toMatchObject({ kind: 'mark', window: held, lease_id: relimitedLease!.lease_id });

  // A sample still showing the window shut in its current period.
  vendorUsage.set(first.token, { short: { utilization: 100, resetsAt: nextReset }, long: weekly });
  await sampledWindow(atReset(nextReset));
  const heldA2 = (await mockHub.seed.chunk(chunk(projects['p-a']!))).body.chunk_id;
  const heldB2 = (await mockHub.seed.chunk(chunk(projects['p-b']!))).body.chunk_id;
  const freeC3 = (await mockHub.seed.chunk(chunk(projects['p-c']!))).body.chunk_id;
  await passUntil(host, () => leasesOf(host, C, freeC3), (leases) => leases.length > 0);
  expect(await leasesOf(host, A, heldA2)).toEqual([]);
  expect(await leasesOf(host, B, heldB2)).toEqual([]);
  expect(await hold(host, A)).toMatchObject({ kind: 'mark', lease_id: relimitedLease!.lease_id });
  await expectUnbraked(host);
  await passUntil(host, () => levers(mockHub, host, A), (seen) => seen.join() === 'floor');

  const cleared = await host.cli(['clear-floor', '--subscription', FIRST]);
  expect(cleared.code, cleared.stdout + cleared.stderr).toBe(0);
  const freeC4 = (await mockHub.seed.chunk(chunk(projects['p-c']!))).body.chunk_id;
  await passUntil(host, () => proceeded(host, { runner: B, chunk: limitedAgain }, [[A, heldA2], [B, heldB2], [C, freeC4]]), Boolean);
  const floor = (await host.runners[A]!.status()).body.throttle?.floor;
  expect(floor).toMatchObject({ admits: true, hold: null, clear: { by: 'operator' } });
  await expectUnbraked(host);
  await passUntil(host, () => levers(mockHub, host, A), (seen) => seen.length === 0);
});
