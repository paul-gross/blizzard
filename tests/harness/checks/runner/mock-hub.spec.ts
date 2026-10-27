import * as fs from 'node:fs';
import { isAlive } from '../../daemons/process';
import { scanLog } from '../../logs';
import { expect, runnerTest as test } from '../../fixtures';

const DEFAULT_TENANT = 'ten_00000000000000000000000000';
const IDENTITY = '/api/fleet/identity';
const oneNode = { entry: 'build', nodes: { build: { prompt: 'build it', choices: [{ name: 'pass', to: 'done' }] } } };

test.describe('independent worlds', () => {
  test.describe.configure({ mode: 'serial' });
  let first: { url: string; pid: number } | undefined;

  test('the first test arms a global lever, seeds a chunk, and is captured', async ({ mockHub }) => {
    first = { url: mockHub.url, pid: mockHub.pid };
    expect((await mockHub.levers.arm('fleet_flags', { payload: { alpha: true } })).ok).toBe(true);
    expect((await mockHub.seed.chunk(oneNode)).status).toBe(201);
    const runner = await mockHub.enroll.runner({ name: 'r-first' });
    expect((await mockHub.fleet(runner.body.token).get(IDENTITY)).status).toBe(200);
    // A second tenant makes a header-less operator call ambiguous, so it is seeded last.
    expect((await mockHub.seed.tenant({ name: 'second' })).status).toBe(201);
    expect((await mockHub.captured.read()).body.requests.length).toBeGreaterThan(0);
  });

  test('the second test sees none of it, and the first test’s mock hub is gone', async ({ mockHub }) => {
    expect(first).toBeDefined();
    expect(mockHub.url).not.toBe(first?.url);
    expect(mockHub.pid).not.toBe(first?.pid);
    expect(isAlive(first?.pid as number)).toBe(false);
    expect((await mockHub.levers.list()).body.active).toEqual([]);
    expect((await mockHub.captured.read()).body.requests).toEqual([]);
    const me = await mockHub.raw.get('/api/me');
    expect(me.body.tenants.map((tenant: { tenant_id: string }) => tenant.tenant_id)).toEqual([DEFAULT_TENANT]);
  });
});

test('a host, a runner under it, and a rotation go through the client; the rotated token alone answers', async ({ mockHub }) => {
  const host = await mockHub.enroll.host({ name: 'xps' });
  expect(host.status).toBe(201);
  const runner = await mockHub.enroll.runner({ name: 'r-one', host_id: host.body.host_id });
  expect(runner.status).toBe(201);
  const before = await mockHub.fleet(runner.body.token).get(IDENTITY);
  expect(before.body.runner_id).toBe(runner.body.runner_id);

  const rotated = await mockHub.enroll.rotate(runner.body.runner_id);
  expect(rotated.status).toBe(201);
  expect(rotated.body.token).not.toBe(runner.body.token);
  const after = await mockHub.fleet(rotated.body.token).get(IDENTITY);
  expect(after.status).toBe(200);
  expect(after.body.runner_id).toBe(runner.body.runner_id);
  const stale = await mockHub.fleet(runner.body.token).get(IDENTITY);
  expect(stale.status).toBe(401);
});

test('an armed lever with remaining shows in the listing and heals; a fleet call’s Authorization is captured', async ({ mockHub }) => {
  const runner = await mockHub.enroll.runner({ name: 'r-lever' });
  const fleet = mockHub.fleet(runner.body.token);
  await mockHub.levers.arm('unreachable', { remaining: 2 });
  const listed = (await mockHub.levers.list()).body.active;
  expect(listed.map((lever) => [lever.kind, lever.remaining])).toEqual([['unreachable', 2]]);

  expect((await fleet.expecting(503).get(IDENTITY)).status).toBe(503);
  expect((await fleet.expecting(503).get(IDENTITY)).status).toBe(503);
  expect((await fleet.get(IDENTITY)).status).toBe(200);
  expect((await mockHub.levers.list()).body.active).toEqual([]);

  const captured = (await mockHub.captured.read()).body.requests;
  const served = captured.filter((request) => request.path === IDENTITY);
  expect(served.length).toBeGreaterThan(0);
  expect(served.every((request) => request.headers.authorization === `Bearer ${runner.body.token}`)).toBe(true);

  await mockHub.levers.arm('delay', { payload: { ms: 1 } });
  await mockHub.levers.clear('delay');
  expect((await mockHub.levers.list()).body.active).toEqual([]);
  await mockHub.levers.arm('delay', { payload: { ms: 1 } });
  await mockHub.levers.reset();
  expect((await mockHub.levers.list()).body.active).toEqual([]);
});

test('stop then start answers health on the same URL under a new pid', async ({ mockHub }) => {
  const { url, pid } = mockHub;
  await mockHub.seed.tenant({ name: 'lost-on-restart' });
  await mockHub.stop();
  expect(isAlive(pid)).toBe(false);
  await mockHub.start();
  expect(mockHub.url).toBe(url);
  expect(mockHub.pid).not.toBe(pid);
  expect((await mockHub.raw.get('/api/health')).status).toBe(200);
  const me = await mockHub.raw.get('/api/me');
  expect(me.body.tenants).toHaveLength(1);
});

test('a kill leaves the process gone, and start brings it back', async ({ mockHub }) => {
  const { pid } = mockHub;
  await mockHub.kill();
  expect(isAlive(pid)).toBe(false);
  await mockHub.start();
  expect((await mockHub.raw.get('/api/health')).status).toBe(200);
  await expect(mockHub.start()).rejects.toThrow(/is running; stop\(\) or kill\(\)/);
});

test('an unexpected 5xx fails the call, naming it and attaching the mock hub log; expecting(503) returns the answer', async ({ mockHub }, testInfo) => {
  const runner = await mockHub.enroll.runner({ name: 'r-diag' });
  const fleet = mockHub.fleet(runner.body.token);
  await mockHub.levers.arm('unreachable');
  await expect(fleet.get(IDENTITY)).rejects.toThrow(new RegExp(`GET .*${IDENTITY} answered 503, a server error`));
  expect(testInfo.attachments.some((attachment) => attachment.name.startsWith('mock hub log around GET'))).toBe(true);
  expect((await fleet.expecting(503).get(IDENTITY)).status).toBe(503);
  // The control plane is exempt from the transport levers, so clearing never trips the diagnostic.
  expect((await mockHub.levers.reset()).ok).toBe(true);
});

const refusalLine = JSON.stringify({ event: 'sweep pass failed', error: 'UnscopedRunnerStatement: SELECT * FROM chunks' });

test('a refusal line in the mock hub log fails the test at teardown', async ({ mockHub }) => {
  // The test passes only by failing: the scan at teardown must reject the line this body wrote.
  test.fail();
  fs.appendFileSync(mockHub.logPath, `${refusalLine}\n`);
  expect(scanLog(mockHub.logPath).map((line) => line.raw)).toEqual([refusalLine]);
});

test('a log without a refusal line passes the teardown scan', async ({ mockHub }) => {
  fs.appendFileSync(mockHub.logPath, `${JSON.stringify({ event: 'sweep pass completed' })}\n`);
  expect(scanLog(mockHub.logPath)).toEqual([]);
});

test.describe('the auth stub', () => {
  test.describe.configure({ mode: 'serial' });
  let served: string | undefined;

  test('answers jwks.json with the given keys, and 404 elsewhere', async ({ authStub, worldContext }) => {
    const key = { kty: 'RSA', kid: 'k1', n: 'abc', e: 'AQAB' };
    const stub = await authStub([key]);
    served = stub.url;
    const jwks = await worldContext.get(`${stub.url}/api/auth/jwks.json`);
    expect(jwks.status()).toBe(200);
    expect(await jwks.json()).toEqual({ keys: [key] });
    expect((await worldContext.get(`${stub.url}/api/auth/session`)).status()).toBe(404);
    expect((await worldContext.post(`${stub.url}/api/auth/jwks.json`)).status()).toBe(404);
    expect(stub.requests().map((request) => `${request.method} ${request.path}`)).toEqual([
      'GET /api/auth/jwks.json',
      'GET /api/auth/session',
      'POST /api/auth/jwks.json',
    ]);
    stub.setKeys([]);
    expect(await (await worldContext.get(`${stub.url}/api/auth/jwks.json`)).json()).toEqual({ keys: [] });
  });

  test('serves an empty key set by default, and the earlier test’s stub is closed', async ({ authStub, worldContext }) => {
    const stub = await authStub();
    expect(await (await worldContext.get(`${stub.url}/api/auth/jwks.json`)).json()).toEqual({ keys: [] });
    expect(served).toBeDefined();
    await expect(worldContext.get(`${served}/api/auth/jwks.json`)).rejects.toThrow(/ECONNREFUSED/);
  });
});
