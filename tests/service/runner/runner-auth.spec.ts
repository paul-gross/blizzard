import { expect, type MockChunkSpec, runnerTest as test } from '../../harness/fixtures';

// A runner presents its bearer token on every hub call, and a runner no hub added presents none and never registers.
// Both read the mock hub's capture against the identity the host's enrollment issued.

const RUNNER = 'runner-local';
const MAX_STEPS = 40;
const WORK_REF = 'toy-api/issues/1';

const SPEC: MockChunkSpec = {
  entry: 'build',
  nodes: {
    build: {
      executor: 'runner',
      session: 'resume',
      judged_by: 'worker',
      prompt: 'print("built")\n',
      judgement_prompt: "verdict('pass', 'built')\n",
      choices: [{ name: 'pass', description: 'built', to: 'deliver' }],
      retries_max: 1,
    },
    deliver: {
      executor: 'hub',
      judgement: { choices: { landed: { description: 'Landed.', to: 'done' } } },
    },
  },
  work_refs: [{ source: 'mock', ref: WORK_REF }],
};

test('test_runner_presents_the_bearer_token_on_every_hub_call', async ({ mockHub, runnerHost }) => {
  const runner = runnerHost.runners[RUNNER]!;
  const token = runnerHost.hubTokens().runners[RUNNER]!;
  expect(token).toMatch(/.+/);
  const seeded = await mockHub.seed.chunk(SPEC);
  expect(seeded.status).toBe(201);
  const chunkId = seeded.body.chunk_id;

  // Enrollment was the fixture's, as an operator; the capture is the runner's own from here.
  await mockHub.captured.reset();
  // The completion landing is read from the runner's own facts, so the test makes no fleet call of its own meanwhile.
  const acked = async (): Promise<boolean> =>
    (await runner.facts()).body.items.some((fact) => fact.kind === 'completion.submitted' && fact.chunk_id === chunkId && fact.acked_at !== null);
  for (let pass = 0; !(await acked()); pass++) {
    if (pass === MAX_STEPS) throw new Error(`the completion was not acknowledged within ${MAX_STEPS} passes\n${runnerHost.logTail()}`);
    expect((await runnerHost.step()).outcome).toBe('completed');
  }

  // The hub's signing keys are public, fetched with no credential by design.
  const requests = (await mockHub.captured.read()).body.requests.filter((request) => request.path !== '/api/auth/jwks.json');
  expect(requests.length).toBeGreaterThan(0);
  for (const request of requests) {
    expect(request.headers.authorization, `${request.method} ${request.path}`).toBe(`Bearer ${token}`);
  }
  // Registration and the claim/completion path rode the same credential.
  const paths = requests.map((request) => request.path);
  expect(paths).toContain('/api/fleet/runners');
  expect(paths).toContain(`/api/fleet/chunks/${chunkId}/completions`);

  // The work-items proxy is a separately built hub call; it carries the same credential, and the payload survives it.
  await mockHub.captured.reset();
  const proxied = await runner.raw.get(`/api/chunks/${chunkId}/work-items`);
  expect(proxied.status).toBe(200);
  expect((proxied.body as { items: { ref: string }[] }).items.map((item) => item.ref)).toEqual([WORK_REF]);
  const forwarded = (await mockHub.captured.read()).body.requests.filter((request) => request.path === `/api/fleet/chunks/${chunkId}/work-items`);
  expect(forwarded.length).toBeGreaterThan(0);
  expect(forwarded.at(-1)!.headers.authorization).toBe(`Bearer ${token}`);
});

test.describe('a runner no hub added', () => {
  test.use({ runnerStarted: false });

  test('test_a_runner_no_hub_added_sends_no_authorization_header_and_never_registers', async ({ mockHub, runnerHost }) => {
    // The host holds no token: the stopped host's tokens are cleared before its first boot.
    runnerHost.clearHubTokens();
    expect(runnerHost.hubTokens()).toEqual({ host: undefined, runners: { [RUNNER]: undefined } });
    await mockHub.captured.reset();
    await runnerHost.restart();

    // The host halts the runner for want of a token rather than call the hub without one.
    await expect(runnerHost.step()).rejects.toThrow(/has no hub token/);
    const halted = (await runnerHost.api.runners()).body.items.find((item) => item.name === RUNNER)!;
    expect(halted).toMatchObject({ state: 'halted' });
    expect(halted.halted_reasons.join('\n')).toMatch(/no hub token/);
    expect(halted.runner_id).toBe(runnerHost.runners[RUNNER]!.id);

    const requests = (await mockHub.captured.read()).body.requests;
    expect(requests.filter((request) => 'authorization' in request.headers)).toEqual([]);
    expect(requests.filter((request) => request.method === 'POST' && request.path === '/api/fleet/runners')).toEqual([]);
  });
});
