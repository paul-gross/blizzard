import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { test as plain } from '@playwright/test';
import { uniqueName } from '../base';
import { HubInternal } from '../client/internal';
import { stopDaemon } from '../daemons/process';
import { runDir } from '../env';
import { expect } from '../fixtures';
import { startStack } from '../hubs';
import { hubTemplate, RUNNER_STORE, runnerStoreTemplate } from '../init-templates';
import { profileEnv } from '../profiles';
import { runOnce } from '../run-once';

/** The migration revision a store file stands at. */
function revisionOf(file: string): string {
  const db = new DatabaseSync(file, { readOnly: true });
  try {
    return (db.prepare('SELECT version_num FROM alembic_version').get() as { version_num: string }).version_num;
  } finally {
    db.close();
  }
}

plain('a restored hub and a cold hub stand at one revision, answer health, and hold one open tenant', async ({ playwright }, testInfo) => {
  const request = await playwright.request.newContext();
  const dirs = { restored: path.join(runDir(), 'fresh', uniqueName(testInfo, 'restored-')), cold: path.join(runDir(), 'fresh', uniqueName(testInfo, 'cold-')) };
  const [restored, cold] = await Promise.all([startStack(dirs.restored, 0, {}, profileEnv('')), startStack(dirs.cold, 0, {}, profileEnv(''), true)]);
  try {
    expect(revisionOf(path.join(dirs.restored, 'hub', 'data', 'hub.db'))).toBe(revisionOf(path.join(dirs.cold, 'hub', 'data', 'hub.db')));
    for (const stack of [restored, cold]) {
      expect((await request.get(`${stack.coordinates.url}/api/health`)).ok()).toBe(true);
      const open = (await new HubInternal(stack.coordinates.url).listTenants()).filter((tenant) => tenant.state === 'open');
      expect(open).toHaveLength(1);
    }
  } finally {
    await request.dispose();
    await Promise.all([restored, cold].flatMap((stack) => stack.coordinates.daemons).map(stopDaemon));
  }
});

plain('the hub template is the documented durable files and nothing the hub writes while running', async () => {
  const template = await hubTemplate();
  expect(fs.existsSync(path.join(template, 'hub', 'blizzard-hub.toml'))).toBe(true);
  expect(fs.existsSync(path.join(template, 'hub', 'data', 'hub.db'))).toBe(true);
  expect(fs.existsSync(path.join(template, 'hub', 'data', 'hub.db-wal'))).toBe(false);
  expect(fs.existsSync(path.join(template, 'hub.log'))).toBe(false);
});

plain('the runner store template is a migrated store with no identity and no sidecar', async () => {
  const store = await runnerStoreTemplate();
  expect(store.endsWith(RUNNER_STORE)).toBe(true);
  expect(fs.existsSync(`${store}-wal`)).toBe(false);
  // Read a copy: opening the template itself would leave a sidecar beside it for the next host to copy around.
  const copy = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'runner-store-')), 'host.db');
  fs.copyFileSync(store, copy);
  try {
    expect(revisionOf(copy)).not.toBe('');
    const tables = new DatabaseSync(copy, { readOnly: true }).prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name <> 'alembic_version'").all() as { name: string }[];
    for (const { name } of tables) {
      const db = new DatabaseSync(copy, { readOnly: true });
      expect((db.prepare(`SELECT COUNT(*) AS n FROM "${name}"`).get() as { n: number }).n, `${name} holds no rows`).toBe(0);
      db.close();
    }
  } finally {
    fs.rmSync(path.dirname(copy), { recursive: true, force: true });
  }
});

plain('two concurrent requests for a template share one build, and a build failure reaches every waiter', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'run-once-'));
  try {
    let built = 0;
    const build = async (): Promise<void> => {
      built += 1;
      await new Promise((resolve) => setTimeout(resolve, 300));
    };
    await Promise.all([runOnce('shared', build, { dir }), runOnce('shared', build, { dir })]);
    expect(built).toBe(1);

    const failing = async (): Promise<void> => {
      await new Promise((resolve) => setTimeout(resolve, 300));
      throw new Error('forced build failure');
    };
    const outcomes = await Promise.allSettled([runOnce('broken', failing, { dir }), runOnce('broken', failing, { dir }), runOnce('broken', failing, { dir })]);
    expect(outcomes.map((outcome) => outcome.status)).toEqual(['rejected', 'rejected', 'rejected']);
    for (const outcome of outcomes) expect((outcome as PromiseRejectedResult).reason.message).toBe('forced build failure');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
