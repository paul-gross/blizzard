import { expect, runnerTest as test, stepUntil } from '../../harness/fixtures';
import type { MockChunkSpec, MockHub, RunnerHost } from '../../harness/fixtures';

// The real runner against the mock OpenCode 2 under each of its failure levers: the service-level ones baked into the
// emitted engine, and the turn-level script helpers a turn's own script calls. Every prompt is a fenced script, since
// the runner composes prose around each turn's message.

const STALL_PASSES = 600;

test.setTimeout(120_000);

const OPENCODE2 = { runner: [{ name: 'runner-local', opencode: { enabled: true, binary: '${mockOpencode2}' } }] };
test.use({ runnerConfig: OPENCODE2 });

function script(...lines: string[]): string {
  return ['<behavior-script>', ...lines, '</behavior-script>', ''].join('\n');
}

/** A one-node chunk whose build runs ``prompt``, judged to done by a passing judgement. */
function single(graphId: string, prompt: string, retries = 1): MockChunkSpec {
  return {
    graph_id: graphId,
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner',
        session: 'resume',
        session_harnesses: ['opencode'],
        judged_by: 'worker',
        prompt,
        judgement_prompt: script("text('<Choice>pass</Choice> judged')"),
        choices: [{ name: 'pass', description: 'built', to: 'done' }],
        retries_max: retries,
      },
    },
  };
}

type Turn = { kind: string; text: string; tool?: { name: string; output?: string | null } | null };

/** Seeds ``spec`` on ``host``, and returns what a case reads its chunk through. */
async function seeded(mockHub: MockHub, host: RunnerHost, spec: MockChunkSpec) {
  const reader = (await mockHub.enroll.runner({ name: 'reader' })).body.token;
  const chunkId = (await mockHub.seed.chunk(spec)).body.chunk_id;
  const runner = host.runners['runner-local']!;
  const chunk = async () => (await mockHub.fleet(reader).get(`/api/fleet/chunks/${chunkId}`)).body as { status: string; escalation: unknown };
  const leases = async () => (await runner.leases()).body.items.filter((lease) => lease.chunk_id === chunkId).sort((a, b) => a.epoch - b.epoch);
  const turns = async (): Promise<Turn[]> => (await Promise.all((await leases()).map(async (lease) => (await runner.leaseTranscript(lease.lease_id)).body.turns as Turn[]))).flat();
  const landed = async () => stepUntil(host, async () => (await chunk()).status, (status) => status === 'done', { stallPasses: STALL_PASSES });
  /** Steps ``host`` by hand, a failing pass included, until ``done`` holds or ``passes`` run out; each pass's outcome. */
  const stepping = async (done: () => Promise<boolean>, passes = 12): Promise<string[]> => {
    const outcomes: string[] = [];
    while (!(await done()) && outcomes.length < passes) outcomes.push((await host.step()).outcome);
    return outcomes;
  };
  return { chunkId, runner, chunk, leases, turns, landed, stepping };
}

test.describe('the service-level levers', () => {
  test('test_opencode2_a_service_that_never_answers_leaves_the_capability_unavailable_by_name', async ({ mockHub, runnerHosts }) => {
    const host = await runnerHosts.create({ runnerOpencode2Levers: ['NEVER_ANSWERS'] });
    const { runner, leases, stepping } = await seeded(mockHub, host, single('gr_oc2_never_answers', script("text('unreached')"), 0));

    await stepping(async () => false, 3);

    // Health started the service and found nothing answering, so the runner never took the chunk to launch it.
    const health = (await runner.harnessHealth()).body.items.find((item) => item.harness_id === 'opencode');
    expect([health?.available, health?.cause], JSON.stringify(health)).toEqual([false, 'config_conflict']);
    expect(await leases(), 'no lease was taken').toEqual([]);
    expect(host.logTail()).toContain('no OpenCode service answers');
  });

  test('test_opencode2_a_dropped_event_stream_still_completes_the_turn_by_polling', async ({ mockHub, runnerHosts }) => {
    const host = await runnerHosts.create({ runnerOpencode2Levers: ['EVENTS_DROPPED'] });
    const { turns, landed } = await seeded(mockHub, host, single('gr_oc2_events_dropped', script("shell('echo polled')", "text('POLLED')")));

    await landed();

    const read = await turns();
    expect(read.some((turn) => turn.tool?.name === 'shell' && (turn.tool.output ?? '').includes('polled'))).toBe(true);
    expect(read.some((turn) => turn.kind === 'asst' && turn.text.includes('POLLED'))).toBe(true);
  });

  test('test_opencode2_an_ignored_interrupt_never_holds_the_lease_open', async ({ mockHub, runnerHosts }) => {
    const host = await runnerHosts.create({ runnerOpencode2Levers: ['INTERRUPT_IGNORED'] });
    const { chunkId, leases, turns } = await seeded(mockHub, host, single('gr_oc2_interrupt_ignored', script("shell('sleep 60')")));
    // The session id is recorded at spawn, before the driver creates the session: wait until its tool runs there.
    await stepUntil(host, turns, (read) => read.some((turn) => turn.tool?.name === 'shell'), { stallPasses: STALL_PASSES });

    expect((await mockHub.seed.stop(chunkId)).status).toBe(200);
    await stepUntil(host, leases, (held) => held.every((lease) => lease.state === 'closed'), { stallPasses: STALL_PASSES });

    // The service answered the interrupt and ran on: the orphan asked once and waited on nothing.
    expect(host.log()).toContain('interrupted an orphaned OpenCode 2 session');
  });

  test('test_opencode2_a_service_restarted_mid_turn_interrupts_the_tool_and_the_chunk_lands', async ({ mockHub, runnerHosts }) => {
    const host = await runnerHosts.create({ runnerOpencode2Levers: ['RESTARTS_MID_TURN'] });
    const { turns, landed } = await seeded(mockHub, host, single('gr_oc2_restart', script("shell('echo after-restart')", "text('RESTARTED')")));

    await landed();

    const tools = (await turns()).filter((turn) => turn.tool?.name === 'shell').map((turn) => turn.tool!.output ?? '');
    expect(tools[0], 'the restart interrupted the tool it held').toContain('Tool execution interrupted');
    expect(tools.some((output) => output.includes('after-restart')), 'a later turn ran it on the new service').toBe(true);
  });

  test('test_opencode2_distorted_shapes_fail_the_turn_and_escalate', async ({ mockHub, runnerHosts }) => {
    const host = await runnerHosts.create({ runnerOpencode2Levers: ['DISTORTED_SHAPES'] });
    const { runner, leases } = await seeded(mockHub, host, single('gr_oc2_distorted', script("text('unread')")));

    const facts = await stepUntil(host, async () => (await runner.facts({ limit: 200 })).body.items.map((fact) => fact.kind), (kinds) => kinds.includes('escalation.recorded'), { stallPasses: STALL_PASSES });

    expect(facts).not.toContain('completion.submitted');
    expect((await leases()).map((lease) => lease.closure_reason)).toEqual(['failed', 'escalated']);
  });

  test('test_opencode2_unknown_part_types_are_passed_over_and_the_turn_completes', async ({ mockHub, runnerHosts }) => {
    const host = await runnerHosts.create({ runnerOpencode2Levers: ['UNKNOWN_PART_TYPES'] });
    const { turns, landed } = await seeded(mockHub, host, single('gr_oc2_unknown_parts', script("shell('echo unseen')", "text('KNOWN')")));

    await landed();

    const read = await turns();
    expect(read.some((turn) => turn.kind === 'asst' && turn.text.includes('KNOWN'))).toBe(true);
    expect(read.some((turn) => turn.tool?.name === 'shell'), 'a tool part of an unknown type is never read as a tool').toBe(false);
  });

  test('test_opencode2_a_listing_with_no_prices_records_usage_at_no_cost', async ({ mockHub, runnerHosts }) => {
    const host = await runnerHosts.create({ runnerOpencode2Levers: ['NO_PRICES'] });
    const { runner, leases, landed } = await seeded(mockHub, host, single('gr_oc2_no_prices', script("text('FREE')")));

    await landed();

    const usage = (await runner.leaseUsage((await leases())[0]!.lease_id)).body.items;
    expect(usage.length).toBeGreaterThan(0);
    expect(usage.every((fact) => (fact.cost_usd ?? 0) === 0), JSON.stringify(usage)).toBe(true);
  });

  test('test_opencode2_a_service_on_another_version_than_its_binary_refuses_the_first_launch_once', async ({ mockHub, runnerHosts }) => {
    const host = await runnerHosts.create({ runnerOpencode2Levers: ['VERSION_MISMATCH'] });
    const { chunk, leases, stepping } = await seeded(mockHub, host, single('gr_oc2_version_mismatch', script("text('MATCHED')")));

    const outcomes = await stepping(async () => (await chunk()).status === 'done', 40);

    // The runner advertised the binary's version until the first launch found the service's: that launch is refused,
    // the version observed afresh, and the next lease runs and records the service's own.
    expect(outcomes[0], 'the refused launch fails its pass').toBe('failed');
    expect((await leases()).map((lease) => [lease.closure_reason, lease.harness_version])).toEqual([
      ['reaped', null],
      ['transitioned', '2.1.5'],
    ]);
  });
});

test.describe('the turn-level script helpers', () => {
  test('test_opencode2_a_usage_limit_marks_the_subscription_and_parks_the_lease', async ({ mockHub, runnerHost }) => {
    const { runner, leases } = await seeded(mockHub, runnerHost, single('gr_oc2_usage_limit', script('usage_limit(retry_after_ms=3_600_000)')));

    const hold = await stepUntil(runnerHost, async () => (await runner.status()).body.throttle?.floor.hold ?? null, (held) => held !== null, { stallPasses: STALL_PASSES });

    expect(hold).toMatchObject({ kind: 'mark', harness_id: 'opencode' });
    expect(hold?.expected_reset, 'the reset the provider named').not.toBeNull();
    expect((await leases()).map((lease) => lease.display_status)).toEqual(['parked']);
  });

  test('test_opencode2_an_overload_backs_the_lease_off', async ({ mockHub, runnerHost }) => {
    const { leases } = await seeded(mockHub, runnerHost, single('gr_oc2_overload', script('overload()')));

    await stepUntil(runnerHost, leases, (held) => held.some((lease) => lease.display_status === 'backing-off'), { stallPasses: STALL_PASSES });
  });

  for (const [name, call] of [
    ['a_provider_refusal', 'provider_refusal()'],
    ['an_unknown_model', 'unknown_model()'],
    ['an_unknown_variant', 'unknown_variant()'],
  ] as const) {
    test(`test_opencode2_${name}_ends_the_turn_with_no_reply_and_the_judgement_decides`, async ({ mockHub, runnerHost }) => {
      const { turns, landed } = await seeded(mockHub, runnerHost, single(`gr_oc2_${name}`, script(call, "text('UNREACHED')")));

      await landed();

      const read = await turns();
      expect(read.some((turn) => turn.kind === 'asst' && turn.text.includes('UNREACHED')), 'the failed turn ran nothing past its failure').toBe(false);
      expect(read.some((turn) => turn.kind === 'asst' && turn.text.includes('<Choice>pass</Choice>'))).toBe(true);
    });
  }

  test('test_opencode2_a_permission_request_is_answered_by_the_driver', async ({ mockHub, runnerHost }) => {
    const ask = script("r = permission_request('external_directory', '/etc/*')", "text('PERMISSION ' + r.status)");
    const { turns, landed } = await seeded(mockHub, runnerHost, single('gr_oc2_permission', ask));

    await landed();

    const read = await turns();
    expect(read.some((turn) => turn.tool?.name === 'external_directory' && turn.tool.output === 'ok')).toBe(true);
    expect(read.some((turn) => turn.kind === 'asst' && turn.text.includes('PERMISSION completed'))).toBe(true);
  });

  test('test_opencode2_a_form_request_is_cancelled_and_the_turn_runs_on', async ({ mockHub, runnerHost }) => {
    const raise = script("f = form('Pick one', [{'name': 'style', 'type': 'text'}])", "text('FORM ' + f['id'])");
    const { turns, landed } = await seeded(mockHub, runnerHost, single('gr_oc2_form', raise));

    await landed();

    expect((await turns()).some((turn) => turn.kind === 'asst' && /FORM frm_/.test(turn.text))).toBe(true);
  });

  test('test_opencode2_a_child_that_outlives_its_roots_idle_wakes_it', async ({ mockHub, runnerHost }) => {
    const child = "shell('sleep 2')\ntext('CHILD DONE')";
    const root = script(`subagent(${JSON.stringify(child)}, background=True, wake="text('WOKEN ' + wake_result())")`, "text('ROOT IDLE')");
    const { turns, landed } = await seeded(mockHub, runnerHost, single('gr_oc2_wake', root));

    await landed();

    const texts = (await turns()).filter((turn) => turn.kind === 'asst').map((turn) => turn.text);
    const idle = texts.findIndex((text) => text.includes('ROOT IDLE'));
    const woken = texts.findIndex((text) => text.includes('WOKEN CHILD DONE'));
    expect(idle, JSON.stringify(texts)).toBeGreaterThanOrEqual(0);
    expect(woken, 'the child woke its root after the root went idle, within the same turn').toBeGreaterThan(idle);
  });
});
