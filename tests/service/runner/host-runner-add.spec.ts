import { parse } from 'smol-toml';

import { expect, runnerTest as test, type RunnerHost } from '../../harness/fixtures';

// A runner added to a running, stepped host over its API: listed `starting` until the host tick starts it, served
// beside the host's first runner from then on and across a restart, and claiming and landing a chunk of its own
// project. The host file is never written.

test.setTimeout(120_000);

const ADDED = 'night-1';
const chunk = {
  entry: 'build',
  nodes: {
    build: { executor: 'runner', session: 'resume', judged_by: 'worker', prompt: "print('worked')\n", judgement_prompt: "verdict('pass', 'worked')\n", choices: [{ name: 'pass', to: 'deliver', description: 'worked' }] },
    deliver: { executor: 'hub', judgement: { choices: { landed: { description: 'done', to: 'done' } } } },
  },
};

async function listed(host: RunnerHost): Promise<Record<string, string>> {
  return Object.fromEntries((await host.api.runners()).body.items.map((item) => [item.name, item.state]));
}

/** The harness settings the host's first runner was given, so the added runner runs the same mock harness. */
function harnessSettings(host: RunnerHost): Record<string, unknown> {
  const first = (parse(host.hostFile()).runner as Record<string, unknown>[])[0]!;
  const claude = first.claude_code as Record<string, unknown>;
  const worker = first.worker as Record<string, unknown>;
  return {
    agents: 1,
    transcripts_root: first.transcripts_root,
    'claude_code.binary': claude.binary,
    'opencode.enabled': false,
    'worker.env_passthrough': worker.env_passthrough,
    ...(first.subscription === undefined ? {} : { subscription: first.subscription }),
  };
}

test('test_a_runner_added_over_the_api_starts_at_the_host_tick_survives_a_restart_and_claims', async ({ mockHub, runnerHosts }) => {
  const project = (await mockHub.seed.project({ slug: 'p-night', name: 'p-night' })).body.project_id;
  const host = await runnerHosts.create();
  const hostFile = host.hostFile();

  const added = await host.api.addRunner({ name: ADDED, projects: [{ project: 'p-night' }], settings: harnessSettings(host) });
  expect(added.status, JSON.stringify(added.body)).toBe(200);
  const view = added.body;
  expect(view.state).toBe('starting');
  expect(await listed(host)).toEqual({ 'runner-local': 'serving', [ADDED]: 'starting' });

  expect((await host.stepHost()).outcome).toBe('completed');
  expect(await listed(host)).toEqual({ 'runner-local': 'serving', [ADDED]: 'serving' });

  await host.restart();
  expect(await listed(host)).toEqual({ 'runner-local': 'serving', [ADDED]: 'serving' });
  expect(host.hostFile()).toBe(hostFile);

  const seeded = await mockHub.seed.chunk({ ...chunk, project_id: project });
  expect(seeded.status).toBe(201);
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  const status = async (): Promise<string> => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${seeded.body.chunk_id}`)).body.status as string;
  for (let pass = 0; pass < 40 && (await status()) !== 'done'; pass++) {
    expect((await host.socketApi.stepRunner(view.runner_id!)).outcome).toBe('completed');
  }
  expect(await status(), host.logTail()).toBe('done');
});
