import { expect, runnerTest as test, stepUntil } from '../../harness/fixtures';

test('a real runner submits the judgement reason to the mock hub', async ({ mockHub, runnerHost }) => {
  const reason = 'Checks passed; the change is ready for review.';
  const seeded = await mockHub.seed.chunk({
    entry: 'build',
    nodes: {
      build: {
        executor: 'runner', session: 'resume', judged_by: 'worker', prompt: "print('worked')\n",
        judgement_prompt: `verdict('pass', '${reason}')\n`,
        choices: [{ name: 'pass', description: 'ready', to: 'done' }],
      },
    },
  });
  expect(seeded.status).toBe(201);
  const id = seeded.body.chunk_id;
  const runner = runnerHost.runners['runner-local']!;
  const completion = () => runner.facts({ limit: 200 }).then((response) =>
    response.body.items.find((fact) => fact.kind === 'completion.submitted' && fact.chunk_id === id));
  await stepUntil(runnerHost, completion, (fact) => fact !== undefined && fact.acked_at !== null);

  const requests = (await mockHub.captured.read()).body.requests.filter((request) =>
    request.method === 'POST' && request.path === `/api/fleet/chunks/${id}/completions`);
  expect(requests.length).toBeGreaterThan(0);
  expect(requests[0]!.body).toMatchObject({ choice: 'pass', reason });
  const detail = await mockHub.fleetOf(runnerHost, 'runner-local').get(`/api/fleet/chunks/${id}`);
  expect(detail.status).toBe(200);
  expect(detail.body.history).toEqual(expect.arrayContaining([
    expect.objectContaining({ choice_name: 'pass', reason }),
  ]));
});
