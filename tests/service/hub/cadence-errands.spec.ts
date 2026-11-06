import { expect, type HubApi, test } from '../../harness/fixtures';

// A cadence's errands read over a real hub on the controlled clock: before any pass an errand waits with the instant it
// comes due; with the clock moved to that instant the step mints it and the errand reads running with its run and no
// due instant; once the run settles it waits again with the next moment. A minted run names its cadence and chore.
// Every claim about time moves the clock and steps the `cadence` sweep through its pass route; nothing waits.

interface Errand {
  chore: string;
  scope: string;
  state: string;
  due_at: string | null;
  last_run: { chunk_id: string | null; status: string | null; scheduled_at: string; finished_at: string | null } | null;
}

async function errands(api: HubApi, cadenceId: string): Promise<Errand[]> {
  const read = await api.get(`/api/cadences/${cadenceId}/errands`);
  expect(read.status, JSON.stringify(read.body)).toBe(200);
  return read.body as Errand[];
}

test.describe('a cadence on a fresh hub with cadences on', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled', hubConfig: { flags: { cadences: true } } });

  test('an errand waits, runs once the clock reaches its due instant, and waits again once its run settles', async ({
    api,
    clock,
    project,
  }) => {
    const routine = await api.post(`/api/projects/${project.slug}/routines`, {
      data: { slug: 'sweep', graph_name: 'garden-routine', default_scope_slug: 'sweep' },
    });
    expect(routine.status, JSON.stringify(routine.body)).toBe(201);
    const start = clock.now();
    const created = await api.post(`/api/projects/${project.slug}/cadences`, {
      data: {
        slug: 'nightly',
        schedule: { cron: '0 20 * * *', zone: 'UTC' },
        chores: [{ key: 'plain', routine: 'sweep' }],
      },
    });
    expect(created.status, JSON.stringify(created.body)).toBe(201);
    const cadence = created.body as { cadence_id: string; chores: { chore_id: string }[] };

    const [waiting] = (await errands(api, cadence.cadence_id)) as [Errand];
    expect([waiting.chore, waiting.scope, waiting.state, waiting.last_run]).toEqual(['plain', 'sweep', 'waiting', null]);
    const dueAt = new Date(waiting.due_at!);
    expect(dueAt.getTime()).toBeGreaterThan(clock.now().getTime());

    await api.sweep('cadence');
    expect((await errands(api, cadence.cadence_id))[0]?.state).toBe('waiting');

    await clock.set(dueAt);
    expect((await errands(api, cadence.cadence_id))[0]?.state).toBe('due');
    await api.sweep('cadence');
    const [running] = (await errands(api, cadence.cadence_id)) as [Errand];
    expect([running.state, running.due_at]).toEqual(['running', null]);
    const chunkId = running.last_run!.chunk_id!;
    expect(chunkId).toBeTruthy();

    const until = new Date(clock.now().getTime() + 1000);
    const listed = await api.get(`/api/runs?since=${start.toISOString()}&until=${until.toISOString()}`);
    const run = (listed.body as { chunk_id: string; cadence: Record<string, string> | null }[]).find((r) => r.chunk_id === chunkId);
    expect(run?.cadence).toEqual({
      cadence_id: cadence.cadence_id,
      cadence_slug: 'nightly',
      cadence_name: 'nightly',
      chore_id: cadence.chores[0]?.chore_id,
      chore_key: 'plain',
    });

    expect((await api.post(`/api/chunks/${chunkId}/stop`, { data: { by: 'e2e-test' } })).status).toBe(202);
    const [settled] = (await errands(api, cadence.cadence_id)) as [Errand];
    expect(settled.state).toBe('waiting');
    expect(new Date(settled.due_at!).getTime()).toBe(dueAt.getTime() + 24 * 3600 * 1000);
    expect(settled.last_run!.finished_at).not.toBeNull();
  });
});
