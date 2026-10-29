import * as fs from 'node:fs';
import * as path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { expect, runnerTest as test } from '../../../harness/fixtures';

const contract = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../../contracts/store/runner.json'), 'utf8')) as {
  tables: Record<string, { columns: Record<string, unknown> }>;
};

function database(dir: string): DatabaseSync {
  return new DatabaseSync(path.join(dir, 'data', 'host.db'));
}

function tokenFor(dir: string, variable: string): string {
  const entry = fs.readFileSync(path.join(dir, '.env'), 'utf8').split('\n').find((line) => line.startsWith(`${variable}=`));
  expect(entry, `the CLI must enroll and write ${variable}`).toBeDefined();
  return entry!.slice(variable.length + 1);
}

function columns(table: string, ...names: string[]): void {
  expect(contract.tables[table]).toBeDefined();
  for (const name of names) expect(contract.tables[table]?.columns[name]).toBeDefined();
}

test('test_a_store_that_names_another_runner_halts_that_runner_while_the_host_serves', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({ runnerStarted: false });
  const runner = host.runners['runner-local']!;
  const foreign = 'rn_01JX00000000000000000HALTB';
  columns('runner_identity', 'runner_id', 'runner_name', 'registered_at');
  const db = database(host.dir);
  try {
    db.prepare('DELETE FROM runner_identity').run();
    db.prepare('INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES (?, ?, ?)').run(foreign, 'runner-local', '2026-10-13T00:00:00+00:00');
  } finally {
    db.close();
  }
  await host.restart();
  expect((await host.api.health()).status).toBe(200);
  expect((await host.api.ready()).body.detail).toContain(foreign);
  expect((await host.api.ready()).body.detail).toContain(runner.id);
  expect((await runner.expecting(503).status()).status).toBe(503);
  expect((await mockHub.runner(tokenFor(host.dir, 'BZ_HUB_TOKEN_RUNNER_LOCAL'), runner.id, 409)).status).toBe(409);
  await host.stop();
  const stopped = database(host.dir);
  try {
    expect(stopped.prepare('SELECT runner_id FROM runner_identity').all()).toEqual([{ runner_id: foreign }]);
  } finally {
    stopped.close();
  }
});

test('test_a_host_with_a_token_reports_itself_and_records_its_identity', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create({ runnerStarted: false, runnerConfig: { runner: [{ name: 'runner-b', projects: [{ project: 'default', workspace: 'workspace-local' }] }] } });
  const token = tokenFor(host.dir, 'BZ_HOST_TOKEN');
  await host.restart();
  expect((await host.stepHost()).outcome).toBe('completed');
  const registration = (await host.api.registration()).body;
  expect(registration.state).toBe('registered');
  const runnerIds = Object.values(host.runners).map((runner) => runner.id).sort();
  expect(registration.held_runner_ids.sort()).toEqual(runnerIds);
  const record = (await mockHub.host(token)).body;
  expect(record.host_id).toBe(registration.host_id);
  expect(record.runners.map((runner) => runner.runner_id).sort()).toEqual(runnerIds);
  expect(record.last_seen_at).not.toBeNull();
  for (const runner of Object.values(host.runners)) {
    await host.step(runner.name);
    const own = (await mockHub.runner(tokenFor(host.dir, `BZ_HUB_TOKEN_${runner.name.toUpperCase().replace(/[^A-Z0-9]/g, '_')}`), runner.id)).body;
    expect(own.bindings).toEqual([{ project: 'default', workspace_id: 'workspace-local', agents: 1, workspace_unreported: false }]);
    expect(own.subscription).toBe('anthropic');
    expect(own.subscription_unrostered).toBe(false);
  }
  await host.stop();
  columns('host_identity', 'host_id');
  const db = database(host.dir);
  try {
    expect(db.prepare('SELECT host_id FROM host_identity').all()).toEqual([{ host_id: registration.host_id }]);
  } finally {
    db.close();
  }
});

test('test_against_a_hub_with_no_hosts_both_runners_work_and_the_host_buffers_its_facts', async ({ mockHub, runnerHosts }) => {
  const project = (await mockHub.seed.project({ slug: 'p-b', name: 'p-b' })).body.project_id;
  const host = await runnerHosts.create({ runnerStarted: false, runnerConfig: { runner: [{ name: 'runner-b', projects: [{ project: 'p-b', workspace: 'workspace-local' }] }] } });
  await mockHub.levers.arm('host_plane_absent');
  await host.restart();
  expect((await host.stepHost()).outcome).toBe('completed');
  expect((await host.api.registration()).body.state).toBe('no_hosts');
  const chunk = { entry: 'build', nodes: { build: { executor: 'runner', prompt: "print('worked')\n", judgement_prompt: "verdict('pass', 'worked')\n", choices: [{ name: 'pass', to: 'done', description: 'worked' }] } } };
  const first = (await mockHub.seed.chunk(chunk)).body.chunk_id;
  await host.step('runner-local');
  const second = (await mockHub.seed.chunk({ ...chunk, project_id: project })).body.chunk_id;
  await host.step('runner-b');
  expect((await host.runners['runner-local']!.leases()).body.items.some((lease) => lease.chunk_id === first)).toBe(true);
  expect((await host.runners['runner-b']!.leases()).body.items.some((lease) => lease.chunk_id === second)).toBe(true);
  expect((await host.api.registration()).body.buffered_facts).toBeGreaterThan(0);
  await host.stop();
  columns('host_identity', 'host_id');
  columns('host_outbound_buffer', 'acked_at', 'kind');
  const db = database(host.dir);
  try {
    expect(db.prepare('SELECT host_id FROM host_identity').all()).toEqual([]);
    expect(db.prepare('SELECT kind FROM host_outbound_buffer WHERE acked_at IS NULL').all().length).toBeGreaterThan(0);
  } finally {
    db.close();
  }
});
