import * as fs from 'node:fs';
import * as path from 'node:path';
import { test as plain } from '@playwright/test';
import { HubInternal } from '../client/internal';
import { isAlive } from '../daemons/process';
import { runDir } from '../env';
import { expect, test } from '../fixtures';
import { hubsFile } from '../hubs';
import { requireHubOptions } from '../tenancy';

plain.describe('the hub options are refused before any hub starts', () => {
  plain('a misspelled hubConfig key is refused, naming the key', () => {
    expect(() => requireHubOptions('fresh-hub', { tracing: { platfrom: true } }, {})).toThrow(/tracing\.platfrom/);
  });

  plain('a hubEnv name the contract sets is refused, naming the variable', () => {
    expect(() => requireHubOptions('fresh-hub', {}, { BLIZZARD_TENANT_GUARD: '0' })).toThrow(/BLIZZARD_TENANT_GUARD/);
  });

  plain('a hubEnv name a profile owns is refused, naming the variable', () => {
    expect(() => requireHubOptions('fresh-hub', {}, { BLIZZARD_TEST_ACTIONS: '1' })).toThrow(/hubEnv BLIZZARD_TEST_ACTIONS/);
    expect(() => requireHubOptions('fresh-hub', {}, { BLIZZARD_CRASH_FENCE: '1' })).toThrow(/hubEnv BLIZZARD_CRASH_FENCE/);
  });

  plain('either option below fresh-hub is refused, naming fresh-hub', () => {
    expect(() => requireHubOptions('fresh-tenant', { tracing: { platform: true } }, {})).toThrow(/hubConfig.*fresh-hub/);
    expect(() => requireHubOptions('common', {}, { OTEL_EXPORTER_OTLP_ENDPOINT: 'http://127.0.0.1:1' })).toThrow(/hubEnv.*fresh-hub/);
  });

  plain('a placeholder no stack address answers to is refused, naming the key', () => {
    expect(() => requireHubOptions('fresh-hub', { auth: { superuser: '${nope}' } }, {})).toThrow(/hubConfig auth\.superuser/);
    expect(() => requireHubOptions('fresh-hub', { auth: { superuser: '${idp}' } }, {})).not.toThrow();
  });

  plain('sound options at fresh-hub, and none anywhere, pass', () => {
    expect(() => requireHubOptions('fresh-hub', { tracing: { platform: true, platform_sample_ratio: 1.0 } }, { OTEL_EXPORTER_OTLP_ENDPOINT: 'http://127.0.0.1:1' })).not.toThrow();
    expect(() => requireHubOptions('common', {}, {})).not.toThrow();
  });
});

// The wiring checks hold under BLIZZARD_ISOLATION_RAISE: the served level is the key, so `fresh-tenant` is served
// `fresh-hub` there and `fresh-hub`'s own options never change.
const raised = process.env.BLIZZARD_ISOLATION_RAISE === '1';

// Served below `fresh-hub`, each of these fails before the test starts; the raised run serves them `fresh-hub`.
test.describe('freshHub at fresh-tenant', () => {
  test.use({ isolation: 'fresh-tenant' });
  test.fail(!raised, 'freshHub names the level it was refused at');
  test('naming the fixture fails', async ({ freshHub }) => {
    void freshHub;
  });
});

test.describe('hubConfig at fresh-tenant', () => {
  test.use({ isolation: 'fresh-tenant', hubConfig: { tracing: { platform: true } } });
  test.fail(!raised, 'hubConfig names fresh-hub');
  test('fails before the test starts', ({ level }) => {
    void level;
  });
});

test.describe('hubConfig at fresh-hub', () => {
  test.use({ isolation: 'fresh-hub', hubConfig: { tracing: { platform: true, platform_sample_ratio: 1.0 } } });

  test('reaches the hub’s config file', ({ testHub }) => {
    const hubToml = fs.readFileSync(path.join(path.dirname(testHub.coordinates.logPath), 'hub', 'blizzard-hub.toml'), 'utf8');
    expect(hubToml).toMatch(/\[tracing\][\s\S]*platform = true/);
  });
});

test.describe('a placeholder in hubConfig', () => {
  test.use({ isolation: 'fresh-hub', hubConfig: { auth: { superuser: '${idp}/who' } } });

  test('expands to the stack’s IdP in the written hub file', ({ testHub }) => {
    const hubToml = fs.readFileSync(path.join(path.dirname(testHub.coordinates.logPath), 'hub', 'blizzard-hub.toml'), 'utf8');
    expect(hubToml).toContain(`superuser = "${testHub.coordinates.idpUrl}/who"`);
    expect(hubToml).not.toContain('${');
  });
});

test.describe('the lifecycle of a fresh hub', () => {
  test.use({ isolation: 'fresh-hub' });

  test('stop resolves with 0, and start answers on the same URL and tenant', async ({ freshHub, testHub, tenant, api }) => {
    const url = testHub.coordinates.url;
    expect(await freshHub.stop()).toBe(0);
    const restarted = await freshHub.start();
    expect(restarted.url).toBe(url);
    expect((await api.get('/api/health')).status).toBe(200);
    const open = (await new HubInternal(url).listTenants()).filter((candidate) => candidate.state === 'open');
    expect(open.map((candidate) => candidate.tenant_id)).toEqual([tenant.id]);
  });

  test('reset is refused while the hub runs; stopped, it hosts empty data on the same URL with a new tenant', async ({ freshHub, testHub, tenant }) => {
    // Three hub lifecycles — the fixture's start, the stop, the reset's re-init and start — take 16–24 s on a CI runner.
    test.slow();
    const url = testHub.coordinates.url;
    await expect(freshHub.reset()).rejects.toThrow(/stop\(\) or kill\(\) it before reset/);
    await freshHub.stop();
    const reset = await freshHub.reset();
    expect(reset.url).toBe(url);
    const open = (await new HubInternal(url).listTenants()).filter((candidate) => candidate.state === 'open');
    expect(open).toHaveLength(1);
    expect(open[0]?.tenant_id).not.toBe(tenant.id);
  });

  test('kill leaves no process', async ({ freshHub }) => {
    const pid = freshHub.pid;
    expect(isAlive(pid)).toBe(true);
    await freshHub.kill();
    expect(isAlive(pid)).toBe(false);
  });

  test('the hubs file records no exit promise', () => {
    const file = hubsFile('service-hub');
    expect(fs.readFileSync(file, 'utf8')).not.toContain('exited');
  });
});

test.describe.serial('a restarted hub is stopped with its test', () => {
  const record = (): string => path.join(runDir(), 'restarted-hub-pids.json');

  test.describe('the test', () => {
    test.use({ isolation: 'fresh-hub' });
    test('restarts its hub', async ({ freshHub, testHub, api }) => {
      const first = freshHub.pid;
      await freshHub.stop();
      await freshHub.start();
      expect(freshHub.pid).not.toBe(first);
      expect((await api.get('/api/health')).status).toBe(200);
      fs.writeFileSync(record(), JSON.stringify([...testHub.coordinates.daemons.map((daemon) => daemon.pid), freshHub.pid]));
    });
  });

  test.describe('afterwards', () => {
    test.use({ isolation: 'common' });
    test('none of its daemons is running, the restarted hub included', () => {
      const pids = JSON.parse(fs.readFileSync(record(), 'utf8')) as number[];
      for (const pid of pids) expect(isAlive(pid), String(pid)).toBe(false);
    });
  });
});
