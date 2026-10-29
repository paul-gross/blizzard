import { expect, type MockChunkSpec, type MockHub, packagedSystemArtifact, type RunnerHost, runnerTest as test } from '../../harness/fixtures';

// A worker's system-scope artifact reads, driven end to end: a real mock worker runs the installed `blizzard runner
// artifact` CLI under the lease credentials the runner injected, against a real runner host and the test's own mock hub.
// The worker states each call's exit code and output in its verdict, which the test reads back from the lease's transcript.

const RUNNER = 'runner-local';
const MAX_STEPS = 40;
const GATE_CHUNK = 'ch_system_artifacts_gate';
const FORMAT = 'garden/finding-format';

test.setTimeout(120_000);

interface CliCall {
  code: number;
  stdout: string;
  stderr: string;
}

/**
 * A worker that waits for the test to arm a lever on `GATE_CHUNK`, runs each `blizzard runner artifact` call in order,
 * then lifts any `unreachable` lever (the hub the host steps against has to come back) and leaves the results in its
 * working directory. Its judgement passes with them as JSON.
 */
function cliWorker(mockHub: MockHub, calls: string[][]): Pick<MockChunkSpec['nodes'][string], 'prompt' | 'judgement_prompt'> {
  const prompt = [
    'import json, subprocess, time, urllib.error, urllib.request',
    'opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))',
    `while not any(lever.get('chunk_id') == '${GATE_CHUNK}' for lever in json.load(opener.open('${mockHub.url}/_levers'))['active']):`,
    '    time.sleep(0.1)',
    'results = []',
    `for args in ${JSON.stringify(calls)}:`,
    "    run = subprocess.run(['blizzard', 'runner', 'artifact', *args], capture_output=True, text=True)",
    "    results.append({'code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr})",
    `opener.open(urllib.request.Request('${mockHub.url}/_levers/unreachable', method='DELETE'))`,
    "with open('system-artifact-reads.json', 'w') as handle:",
    '    json.dump(results, handle)',
    '',
  ].join('\n');
  const judgement_prompt = ["with open('system-artifact-reads.json') as handle:", "    verdict('pass', handle.read())", ''].join('\n');
  return { prompt, judgement_prompt };
}

function chunkRunning(worker: ReturnType<typeof cliWorker>): MockChunkSpec {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        ...worker,
        choices: [{ name: 'pass', description: 'read', to: 'done' }],
        retries_max: 1,
      },
    },
    work_refs: [{ source: 'mock', ref: 'toy-api/issues/1' }],
  };
}

/** Seeds a chunk whose worker makes `calls` and steps the host until its lease is running; returns the lease id. */
async function spawnWorker(mockHub: MockHub, host: RunnerHost, calls: string[][]): Promise<string> {
  const seeded = await mockHub.seed.chunk(chunkRunning(cliWorker(mockHub, calls)));
  expect(seeded.status).toBe(201);
  const runner = host.runners[RUNNER]!;
  for (let pass = 0; ; pass++) {
    if (pass === MAX_STEPS) throw new Error(`no worker spawned within ${MAX_STEPS} passes\n${host.logTail()}`);
    expect((await host.step()).outcome).toBe('completed');
    const lease = (await runner.leases()).body.items.find((item) => item.chunk_id === seeded.body.chunk_id && item.state === 'running');
    if (lease !== undefined) return lease.lease_id;
  }
}

/** Opens the gate the worker's judgement waits on, steps the host to the verdict, and reads the results it passed with from the lease's transcript. */
async function release(mockHub: MockHub, host: RunnerHost, leaseId: string): Promise<CliCall[]> {
  await mockHub.levers.arm('stale_envelope', { chunk_id: GATE_CHUNK });
  const runner = host.runners[RUNNER]!;
  let verdictText: string | undefined;
  await expect
    .poll(
      async () => {
        expect((await host.step()).outcome).toBe('completed');
        const turns = (await runner.leaseTranscript(leaseId)).body.turns;
        verdictText = turns.find((turn) => turn.kind === 'asst' && turn.text?.includes('"code"'))?.text ?? undefined;
        return verdictText !== undefined;
      },
      { message: 'the worker passed with its reads', timeout: 90_000 },
    )
    .toBe(true);
  const text = verdictText!;
  return JSON.parse(text.slice(text.indexOf('['))) as CliCall[];
}

test('test_system_scope_list_and_get_serve_the_seeded_set_end_to_end', async ({ mockHub, runnerHost }) => {
  const seeded = await mockHub.seed.systemArtifact({ name: FORMAT, content: 'the finding format text' });
  expect(seeded.status).toBe(201);

  const leaseId = await spawnWorker(mockHub, runnerHost, [
    ['list', '--scope', 'system'],
    ['get', FORMAT, '--scope', 'system'],
    ['get', FORMAT, '--scope', 'system', '--content'],
  ]);
  const [listed, gotJson, gotContent] = await release(mockHub, runnerHost, leaseId);

  expect(listed!.code, listed!.stderr).toBe(0);
  expect((JSON.parse(listed!.stdout) as { name: string }[]).map((artifact) => artifact.name)).toContain(FORMAT);
  expect(gotJson!.code, gotJson!.stderr).toBe(0);
  expect(JSON.parse(gotJson!.stdout)).toMatchObject({ name: FORMAT, scope: 'system' });
  expect(gotContent!.code, gotContent!.stderr).toBe(0);
  expect(gotContent!.stdout).toBe('the finding format text');
});

test('test_system_scope_read_fails_rather_than_resolving_locally_when_the_hub_is_unreachable', async ({ mockHub, runnerHost }) => {
  // The system scope has no runner-local fallback, so the read outwaits the runner's bounded hub retry before it fails.
  test.slow();
  const seeded = await mockHub.seed.systemArtifact({ name: FORMAT, content: 'the finding format text' });
  expect(seeded.status).toBe(201);

  const leaseId = await spawnWorker(mockHub, runnerHost, [
    ['list', '--scope', 'system'],
    ['get', FORMAT, '--scope', 'system', '--content'],
  ]);
  // No step runs past here, so the lease stays open while the hub is down.
  await mockHub.levers.arm('unreachable', { remaining: 10_000 });
  const [listed, got] = await release(mockHub, runnerHost, leaseId);

  expect(listed!.code).not.toBe(0);
  expect(got!.code).not.toBe(0);
  expect(got!.stdout).toBe('');
  expect(`${got!.stdout}${got!.stderr}`).toContain('could not read');
});

test('test_a_worker_on_a_graph_with_no_artifacts_block_reads_a_packaged_name_verbatim', async ({ mockHub, runnerHost }) => {
  // The bytes a worker gets back are exactly the packaged document's own text, not a stand-in string.
  const packagedText = packagedSystemArtifact(FORMAT);
  const seeded = await mockHub.seed.systemArtifact({ name: FORMAT, content: packagedText });
  expect(seeded.status).toBe(201);

  // The chunk declares no graph artifacts, as a graph with no `artifacts:` block leaves it.
  const leaseId = await spawnWorker(mockHub, runnerHost, [['get', '--scope', 'system', FORMAT, '--content']]);
  const [got] = await release(mockHub, runnerHost, leaseId);

  expect(got!.code, got!.stderr).toBe(0);
  expect(got!.stdout).toBe(packagedText);
});
