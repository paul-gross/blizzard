import * as fs from 'node:fs';
import * as http from 'node:http';
import * as path from 'node:path';
import { test as plain, expect as plainExpect } from '@playwright/test';
import { hubApi } from '../client/clients';
import { Ledger } from '../client/ledger';
import { isAlive } from '../daemons/process';
import { runDir } from '../env';
import { expect, test } from '../fixtures';
import { LEVELS, type Level, resolveLevel } from '../tenancy';

// A test that never sets `isolation` fails before it starts, naming the option.
test.fail('a test that sets no isolation fails before it starts', async ({ api }) => {
  void api;
});

plain('the failure for an unset isolation names the option and the levels', () => {
  plainExpect(() => resolveLevel(undefined, false)).toThrow(/`isolation`.*'common' \| 'fresh-project' \| 'fresh-tenant' \| 'fresh-hub'/);
});

plain('the cross-check serves one level above the declared one, and fresh-hub stays fresh-hub', () => {
  expect(LEVELS.map((level) => resolveLevel(level, true).served)).toEqual(['fresh-project', 'fresh-tenant', 'fresh-hub', 'fresh-hub']);
  expect(LEVELS.map((level) => resolveLevel(level, false).served)).toEqual([...LEVELS]);
  expect(resolveLevel('common', true)).toEqual({ declared: 'common', served: 'fresh-project' });
});

// What the `level` fixture serves follows BLIZZARD_ISOLATION_RAISE, so these hold in both runs of the cross-check.
const raised = process.env.BLIZZARD_ISOLATION_RAISE === '1';

test.describe('a test declared common', () => {
  test.use({ isolation: 'common' });

  test('is served one level up under the flag, and records the level it declared', async ({ level }, testInfo) => {
    expect(level).toEqual({ declared: 'common', served: raised ? 'fresh-project' : 'common' });
    expect(testInfo.annotations).toContainEqual({ type: 'isolation-level', description: 'common' });
  });
});

test.describe('a test declared fresh-hub', () => {
  test.use({ isolation: 'fresh-hub' });

  test('is served at fresh-hub with the flag set or not', async ({ level }) => {
    expect(level).toEqual({ declared: 'fresh-hub', served: 'fresh-hub' });
  });
});

const levels: Record<Level, string> = {
  common: 'the hub’s common tenant and its default project',
  'fresh-project': 'the common tenant and a project created for the test',
  'fresh-tenant': 'a tenant created for the test and its default project',
  'fresh-hub': 'a hub of its own, its seeded tenant and default project',
};

for (const declared of LEVELS) {
  test.describe(`${declared}: ${levels[declared]}`, () => {
    test.use({ isolation: declared });

    test('tenant and project resolve as the level says', async ({ level, tenant, project, api, hub: shared, testHub, adminClient }) => {
      expect(level).toEqual({ declared, served: declared });
      const common = shared.coordinates().commonTenantId;

      if (declared === 'common' || declared === 'fresh-project') {
        expect(tenant).toEqual({ id: common, name: 'common' });
        expect(testHub.coordinates.url).toBe(shared.coordinates().url);
      } else if (declared === 'fresh-tenant') {
        expect(tenant.id).not.toBe(common);
        expect(testHub.coordinates.url).toBe(shared.coordinates().url);
        expect((await adminClient().listTenants()).map((candidate) => candidate.tenant_id)).toContain(tenant.id);
      } else {
        expect(testHub.fresh).toBe(true);
        expect(testHub.coordinates.url).not.toBe(shared.coordinates().url);
        expect(tenant.name).toBe('default');
      }

      if (declared === 'fresh-project') {
        expect(project.slug).not.toBe('default');
        // The client narrows the project list to what the test made, so the one project in it is this one.
        const listed = await api.get('/api/projects');
        expect(listed.body.projects.map((item: { project_id: string }) => item.project_id)).toEqual([project.id]);
      } else {
        expect(project.slug).toBe('default');
      }
    });
  });
}

test.describe.serial('a fresh hub starts for the test and stops with it', () => {
  const record = (): string => path.join(runDir(), 'fresh-hub-pids.json');

  test.describe('the test', () => {
    test.use({ isolation: 'fresh-hub' });
    test('runs against a hub of its own', async ({ testHub, api }) => {
      for (const daemon of testHub.coordinates.daemons) expect(isAlive(daemon.pid), daemon.name).toBe(true);
      fs.writeFileSync(record(), JSON.stringify(testHub.coordinates.daemons.map((daemon) => daemon.pid)));
      expect((await api.get('/api/health')).status).toBe(200);
    });
  });

  test.describe('afterwards', () => {
    test.use({ isolation: 'common' });
    test('none of its daemons is running', () => {
      const pids = JSON.parse(fs.readFileSync(record(), 'utf8')) as number[];
      expect(pids).toHaveLength(3);
      for (const pid of pids) expect(isAlive(pid), String(pid)).toBe(false);
    });
  });
});

test.describe('a server error fails the test that caused it', () => {
  // No hub route answers 5xx on demand, so the client is pointed at a stub that always does.
  plain('a 5xx rejects naming the call, attaches the response, and comes back when expected', async ({ playwright }, testInfo) => {
    const server = http.createServer((_request, response) => {
      response.writeHead(500, { 'content-type': 'text/plain' });
      response.end('stub went wrong');
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const context = await playwright.request.newContext({ baseURL: `http://127.0.0.1:${(server.address() as { port: number }).port}` });
    try {
      const api = hubApi({ context, testInfo, tenantId: 'ten_stub', narrow: false, level: 'common', ledger: new Ledger(), logContext: () => 'a log line' });

      await plainExpect(api.get('/api/chunks')).rejects.toThrow(/GET \/api\/chunks answered 500/);
      expect(testInfo.attachments.map((attachment) => attachment.name)).toEqual(['GET /api/chunks → 500', 'hub log around GET /api/chunks']);
      expect(testInfo.attachments[0]?.body?.toString()).toBe('stub went wrong');

      const answered = await api.expecting(500).get('/api/chunks');
      expect(answered.status).toBe(500);
      expect(answered.body).toBe('stub went wrong');
      await plainExpect(api.expecting(503).get('/api/chunks')).rejects.toThrow(/answered 500/);
    } finally {
      await context.dispose();
      server.close();
    }
  });
});

test.describe('the same client runs against the real shared hub at every level', () => {
  test.use({ isolation: 'common' });
  test('a call answers through the tenant header', async ({ api }) => {
    const answered = await api.get('/api/projects');
    expect(answered.status).toBe(200);
  });
});
