import * as fs from 'node:fs';
import * as http from 'node:http';
import * as path from 'node:path';
import { expect, runnerTest as test } from '../../fixtures';
import type { RunnerHost } from '../../fixtures';

const MAX_STEPS = 40;

/** A one-node chunk whose worker runs `script` and judges by `judgement`, then a hub node that ends it. */
function workerChunk(script: string, judgement = "verdict('pass', 'done')\n"): object {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        judged_by: 'worker',
        prompt: script,
        judgement_prompt: judgement,
        choices: [{ name: 'pass', description: 'done', to: 'deliver' }],
      },
      deliver: { executor: 'hub', judgement: { choices: { landed: { description: 'Landed.', to: 'done' } } } },
    },
  };
}

/** Steps the host until `read` answers something other than `undefined`, failing after `MAX_STEPS` passes. */
async function stepUntil<T>(host: RunnerHost, read: () => Promise<T | undefined>): Promise<T> {
  for (let pass = 0; pass < MAX_STEPS; pass++) {
    const state = await read();
    if (state !== undefined) return state;
    await host.step();
  }
  throw new Error(`not reached within ${MAX_STEPS} passes\n${host.logTail()}`);
}

function filesUnder(dir: string): string[] {
  return fs.existsSync(dir) ? fs.readdirSync(dir, { recursive: true, withFileTypes: true }).filter((entry) => entry.isFile()).map((entry) => entry.name) : [];
}

test('a worker’s transcript lands under the host’s transcripts root, the lease transcript reads it, and rotation removes it', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners['runner-local']!;
  const chunkId = (await mockHub.seed.chunk(workerChunk("print('worked')\n") as never)).body.chunk_id;
  const lease = await stepUntil(runnerHost, async () => (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId && item.state === 'closed'));

  expect(filesUnder(runnerHost.transcriptsRoot).length).toBeGreaterThan(0);
  const transcript = await runner.leaseTranscript(lease.lease_id);
  expect(transcript.status).toBe(200);
  expect(transcript.body).toMatchObject({ lease_id: lease.lease_id, available: true });

  runnerHost.rotateTranscripts();
  expect(fs.existsSync(runnerHost.transcriptsRoot)).toBe(false);
});

test('a recorded worker credential authorizes a lease call, and none is refused', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners['runner-local']!;
  // The mock records a session when its turn ends, so the spawn ends and the judgement holds the lease open long enough
  // to be called as; the host's teardown ends it sooner.
  const chunkId = (await mockHub.seed.chunk({ ...workerChunk("print('worked')\n", 'import time\ntime.sleep(120)\n'), graph_artifacts: [{ name: 'brief', content: 'hello' }] } as never)).body.chunk_id;
  const lease = await stepUntil(runnerHost, async () => (await runner.leases()).body.items.find((item) => item.chunk_id === chunkId && item.state === 'running'));
  // The runner re-mints a lease's token for each resume, so the record is read until the credential it holds answers.
  const headers = await stepUntil(runnerHost, async () => {
    try {
      const candidate = runnerHost.workerHeaders(lease.lease_id);
      return (await runner.leaseArtifacts(lease.lease_id, { scope: 'graph', headers: candidate })).status === 200 ? candidate : undefined;
    } catch {
      return undefined;
    }
  });

  expect(headers.Authorization).toMatch(/^Bearer .+/);
  const artifacts = await runner.leaseArtifacts(lease.lease_id, { scope: 'graph', headers });
  expect(artifacts.status).toBe(200);
  expect(artifacts.body.map((artifact) => artifact.name)).toEqual(['brief']);
  expect((await runner.leaseArtifacts(lease.lease_id, { scope: 'graph' })).status).toBe(403);
  expect(() => runnerHost.workerHeaders('ls_nonesuch')).toThrow(/no mock session .* lease ls_nonesuch/);
});

test('the issued hub tokens read back from .env, and each authenticates a fleet call as its holder', async ({ mockHub, runnerHost }) => {
  const tokens = runnerHost.hubTokens();
  expect(tokens.host).toMatch(/.+/);
  expect(tokens.runners['runner-local']).toMatch(/.+/);
  expect(tokens.runners['runner-local']).not.toBe(tokens.host);

  const identity = await mockHub.fleetOf(runnerHost, 'runner-local').get('/api/fleet/identity');
  expect(identity.status).toBe(200);
  expect(identity.body.runner_id).toBe(runnerHost.runners['runner-local']!.id);
  await mockHub.captured.reset();
  await mockHub.fleetOf(runnerHost).get('/api/fleet/host/runners');
  expect((await mockHub.captured.read()).body.requests.map((request) => request.headers.authorization)).toEqual([`Bearer ${tokens.host}`]);
});

test('a captured request carries its parsed JSON body', async ({ mockHub, runnerHost }) => {
  await runnerHost.step();
  const register = (await mockHub.captured.read()).body.requests.find((request) => request.method === 'POST' && request.path === '/api/fleet/runners');
  expect(register?.body).toEqual(expect.objectContaining({ version: expect.any(String) }));
});

test('reconfigure refuses an undeclared key or a running host, and a sound overlay survives a restart', async ({ runnerHost }) => {
  expect(() => runnerHost.reconfigure({ runner: [{ name: 'runner-local', agents: 2n }] })).toThrow(/stop\(\) it before reconfigure/);
  await runnerHost.stop();
  expect(() => runnerHost.reconfigure({ nonsense_key: 'x' })).toThrow(/nonsense_key: not a key the host declares/);
  expect(() => runnerHost.reconfigure({ port: 1n })).toThrow(/port: the CLI or the fixture owns it/);
  const moved = path.join(path.dirname(runnerHost.dir), 'transcripts-moved');
  runnerHost.reconfigure({ runner: [{ name: 'runner-local', agents: 2n, transcripts_root: moved }] });

  await runnerHost.restart();
  expect((await runnerHost.step()).outcome).toBe('completed');
  const runners = (await runnerHost.api.runners()).body.items;
  expect(runners.find((item) => item.name === 'runner-local')).toBeDefined();
  expect(fs.readFileSync(path.join(runnerHost.dir, 'blizzard-host.toml'), 'utf8')).toMatch(/agents = 2/);
  expect(runnerHost.transcriptsRoot).toBe(moved);
});

test('the whole host log reads from its first line, and holds the tail', async ({ runnerHost }) => {
  await runnerHost.step();
  const log = runnerHost.log();
  expect(log).toBe(fs.readFileSync(runnerHost.logPath, 'utf8'));
  expect(log.length).toBeGreaterThanOrEqual(runnerHost.logTail().length);
  expect(log).toContain(runnerHost.logTail().trim().split('\n').at(-1));
});

test('escalations read by state: the open set, and a closed page under a limit', async ({ runnerHost }) => {
  const runner = runnerHost.runners['runner-local']!;
  expect((await runner.escalations()).body.items).toEqual([]);
  const closed = await runner.escalations({ state: 'closed', limit: 5 });
  expect(closed.status).toBe(200);
  expect(closed.body.items).toEqual([]);
  expect((await runner.expecting(422).escalations({ state: 'closed', limit: 0 })).status).toBe(422);
});

test.describe('a hung OTLP sink', () => {
  test.use({ runnerOtlp: 'hung', runnerStarted: false });

  test('accepts an export’s connection and holds it open unanswered', async ({ runnerHost }) => {
    const sink = runnerHost.otlp!;
    const answered = new Promise<number | undefined>((resolve, reject) => {
      const request = http.request(`${sink.endpoint}/v1/traces`, { method: 'POST', headers: { 'content-type': 'application/x-protobuf' } }, (response) =>
        resolve(response.statusCode),
      );
      request.once('error', reject);
      request.end();
    });
    await expect.poll(() => sink.openConnections()).toBe(1);
    // The request was read in full and still has no answer; closing the sink is what ends it, with no status.
    await sink.close();
    await expect(answered).rejects.toThrow(/socket hang up|ECONNRESET/);
    expect(sink.spans()).toEqual([]);
  });
});

test.describe('a mock hub offering an IdP from its start', () => {
  test.use({ mockHubOfferIdp: true });

  test('serves jwks.json before any lever, and across a restart', async ({ mockHub }) => {
    expect((await mockHub.raw.get('/api/auth/jwks.json')).body).toEqual({ keys: [] });
    await mockHub.levers.reset();
    await mockHub.stop();
    await mockHub.start();
    expect((await mockHub.raw.get('/api/auth/jwks.json')).status).toBe(200);
  });
});

test('a default mock hub offers no IdP', async ({ mockHub }) => {
  expect((await mockHub.raw.get('/api/auth/jwks.json')).status).toBe(404);
});
