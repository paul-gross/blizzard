import * as path from 'node:path';
import { expect, runnerTest as test, type MockChunkSpec, type MockHubClient, type RunnerHost } from '../../harness/fixtures';

const UNBOUND = 'special-harness';
const MAX_STEPS = 50;
const REPO = 'toy-api';
const BRANCH = 'feature/mixed-dispatch';
const PWD = "tool_call('Bash', {'command': 'pwd'}, output=os.getcwd())";
const BUILD = [
  'import os, pathlib, subprocess',
  PWD,
  `repo = str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / '${REPO}')`,
  `subprocess.run(['git', '-C', repo, 'switch', '-C', '${BRANCH}'], check=True, capture_output=True)`,
  "(pathlib.Path(repo) / 'LANDED.md').write_text('landed by the mock harness\\n')",
  "subprocess.run(['git', '-C', repo, 'add', '-A'], check=True, capture_output=True)",
  "subprocess.run(['git', '-C', repo, '-c', 'user.email=mock@blizzard.local', '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: land a change'], check=True, capture_output=True)",
  `subprocess.run(['git', '-C', repo, 'push', 'origin', '${BRANCH}'], check=True, capture_output=True)`,
].join('\n');
const JUDGE = `import os\n${PWD}\nverdict('pass', 'the change is committed')\n`;

function chunk(harnesses?: string[]): MockChunkSpec {
  return {
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner', session: 'resume', judged_by: 'worker',
        ...(harnesses === undefined ? {} : { session_harnesses: harnesses }),
        prompt: BUILD, judgement_prompt: JUDGE,
        choices: [{ name: 'pass', description: 'committed and green', to: 'deliver' }], retries_max: 1,
      },
      deliver: { executor: 'hub', judgement: { choices: { landed: { description: 'landed', to: 'done' } } } },
    },
  };
}

async function reader(hub: MockHubClient) {
  const token = (await hub.enroll.runner({ name: 'reader' })).body.token;
  return async (id: string): Promise<string> => (await hub.fleet(token).get(`/api/fleet/chunks/${id}`)).body.status as string;
}

async function drive(host: RunnerHost, status: () => Promise<string>, target = 'done') {
  let current = await status();
  for (let pass = 0; current !== target && pass < MAX_STEPS; pass++) {
    await host.step();
    current = await status();
  }
  expect(current, host.logTail()).toBe(target);
}

function options(strict = false) {
  return { runnerConfig: { transcripts: { ship: true }, runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockBin}/mock-opencode' }, queue: { strict } }] } };
}

async function leases(host: RunnerHost, id: string) {
  return (await host.runners['runner-local']!.leases()).body.items.filter((item) => item.chunk_id === id);
}

test('test_under_strict_an_incompatible_head_starves_the_workable_entry_behind_it_until_it_clears', async ({ mockHub, runnerHosts }) => {
  const incompatible = (await mockHub.seed.chunk(chunk([UNBOUND]))).body.chunk_id;
  const workable = (await mockHub.seed.chunk(chunk())).body.chunk_id;
  const status = await reader(mockHub);
  const host = await runnerHosts.create(options(true));
  for (let pass = 0; pass < 5; pass++) await host.step();
  expect(await status(incompatible)).toBe('ready');
  expect(await status(workable)).toBe('ready');
  expect(await leases(host, incompatible)).toEqual([]);
  expect(await leases(host, workable)).toEqual([]);
  expect((await mockHub.seed.stop(incompatible)).status).toBe(200);
  await drive(host, () => status(workable));
  expect(await leases(host, workable)).toHaveLength(1);
});

test('test_by_default_the_matched_peek_passes_over_an_incompatible_head_to_the_workable_entry', async ({ mockHub, runnerHosts }) => {
  const incompatible = (await mockHub.seed.chunk(chunk([UNBOUND]))).body.chunk_id;
  const workable = (await mockHub.seed.chunk(chunk())).body.chunk_id;
  const status = await reader(mockHub);
  const host = await runnerHosts.create(options());
  await drive(host, () => status(workable));
  expect(await status(incompatible)).toBe('ready');
  expect(await leases(host, incompatible)).toEqual([]);
});

for (const [title, harnesses, model, expected] of [
  ['test_preference_order_resolves_the_first_available_member_over_the_configured_default', ['opencode', 'claude_code'], undefined, 'opencode'],
  ['test_preference_order_skips_an_unresolvable_member_and_falls_through_to_the_next', [UNBOUND, 'claude_code'], undefined, 'claude_code'],
  ['test_a_native_only_multi_harness_session_is_matched_by_the_reported_supported_models', ['opencode', 'claude_code'], ['opus-unreported', 'openai/gpt-5'], 'opencode'],
  ['test_a_bare_node_with_no_declared_preference_resolves_through_this_runners_configured_default', undefined, undefined, 'claude_code'],
] as const) {
  test(title, async ({ mockHub, runnerHosts }) => {
    const spec = chunk(harnesses === undefined ? undefined : [...harnesses]);
    if (model !== undefined) spec.nodes.build!.session_model = [...model];
    const id = (await mockHub.seed.chunk(spec)).body.chunk_id;
    const status = await reader(mockHub);
    const host = await runnerHosts.create(options());
    await drive(host, () => status(id));
    expect((await leases(host, id)).map((lease) => lease.harness_id)).toEqual([expected]);
  });
}

test('test_a_bare_node_still_honors_the_chunks_own_declared_default', async ({ mockHub, runnerHosts }) => {
  const incompatible = (await mockHub.seed.chunk({ ...chunk(), default_harnesses: [UNBOUND] })).body.chunk_id;
  const compatible = (await mockHub.seed.chunk({ ...chunk(), default_harnesses: ['opencode'] })).body.chunk_id;
  const status = await reader(mockHub);
  const host = await runnerHosts.create(options(true));
  for (let pass = 0; pass < 5; pass++) await host.step();
  expect(await status(incompatible)).toBe('ready');
  expect(await status(compatible)).toBe('ready');
  expect(await leases(host, incompatible)).toEqual([]);
  expect(await leases(host, compatible)).toEqual([]);
  expect((await mockHub.seed.stop(incompatible)).status).toBe(200);
  await drive(host, () => status(compatible));
  // The mock hub admits the chunk default during matching but does not project it onto the bare node at spawn.
  expect((await leases(host, compatible)).map((lease) => lease.harness_id)).toEqual(['claude_code']);
});

test('test_two_session_lineages_interleave_on_one_dispatch_loop_with_no_cross_talk', async ({ mockHub, runnerHosts }) => {
  const claudeSpec = chunk(['claude_code']);
  claudeSpec.nodes.build!.prompt = BUILD.replace(PWD, `${PWD}\ntool_call('Skill', {'skill': 'wf-commit'}, output='ran the commit skill')`);
  const opencodeSpec = chunk(['opencode']);
  opencodeSpec.nodes.build!.prompt = BUILD.replace(PWD, `${PWD}\ntool_call('task', {'subagent_type': 'reviewer'}, output='spawned a sub-agent')`);
  const claude = (await mockHub.seed.chunk(claudeSpec)).body.chunk_id;
  const opencode = (await mockHub.seed.chunk(opencodeSpec)).body.chunk_id;
  const status = await reader(mockHub);
  const host = await runnerHosts.create(options());
  for (let pass = 0; pass < MAX_STEPS; pass++) {
    if (await status(claude) === 'done' && await status(opencode) === 'done') break;
    await host.step();
  }
  expect([await status(claude), await status(opencode)]).toEqual(['done', 'done']);
  const runner = host.runners['runner-local']!;
  const first = await leases(host, claude);
  const second = await leases(host, opencode);
  expect(first).toHaveLength(1);
  expect(second).toHaveLength(1);
  expect(first[0]).toMatchObject({ harness_id: 'claude_code', chunk_id: claude });
  expect(second[0]).toMatchObject({ harness_id: 'opencode', chunk_id: opencode });
  expect(first[0]!.lease_id).not.toBe(second[0]!.lease_id);
  expect(first[0]!.session_id).toBeTruthy();
  expect(second[0]!.session_id).toBeTruthy();
  expect(first[0]!.session_id).not.toBe(second[0]!.session_id);
  for (const [id, lease, harness, normalizer] of [
    [claude, first[0]!, 'claude_code', 'claude-code-jsonl/2'],
    [opencode, second[0]!, 'opencode', 'opencode-export/1'],
  ] as const) {
    const usage = (await runner.chunkUsage(id)).body.items;
    expect(new Set(usage.map((entry) => entry.kind))).toEqual(new Set(['spawn', 'judge']));
    expect(usage.every((entry) => entry.chunk_id === id && entry.lease_id === lease.lease_id && entry.harness_id === harness)).toBe(true);
    const segments = (await runner.transcriptSegments(id)).body.segments;
    expect(segments.length).toBeGreaterThanOrEqual(1);
    expect(segments.every((segment) => segment.harness_id === harness && segment.normalizer_version === normalizer), JSON.stringify(segments)).toBe(true);
    expect(segments.some((segment) => segment.spawn_generation === 1)).toBe(true);
    const expectedCwd = path.join(host.workspace, id);
    let pwdTurns = 0;
    const toolNames: string[] = [];
    for (const segment of segments) {
      const turns = (await runner.transcript(id, segment.segment_id)).body.turns;
      toolNames.push(...turns.flatMap((turn) => turn.kind === 'tool' && turn.tool !== null ? [turn.tool.name] : []));
      const observed = turns.filter((turn) => turn.kind === 'tool' && turn.tool?.input.command === 'pwd').map((turn) => turn.tool?.output);
      expect(observed, `no retained pwd for ${id}/${segment.segment_id}`).toContain(expectedCwd);
      pwdTurns += observed.length;
    }
    expect(pwdTurns).toBeGreaterThanOrEqual(2);
    expect(toolNames).toContain(harness === 'claude_code' ? 'Skill' : 'task');
  }
});
