import { expect, runnerTest as test } from '../../harness/fixtures';

// `GET /api/health` is the probe a browser makes of a host's candidate origins: it answers without sign-in, names the
// host by the id its hub minted, and admits only the host's own hub origin cross-origin.

interface Health {
  status: string;
  host_id: string | null;
}

test('test_health_names_the_host_by_its_hub_minted_id_once_a_report_is_answered', async ({ runnerHosts }) => {
  const host = await runnerHosts.create();
  const before = await host.api.health();
  expect(before.status).toBe(200);
  const unregistered = (await host.api.registration()).body as { host_id: string | null };
  expect((before.body as unknown as Health).host_id).toBe(unregistered.host_id);

  expect((await host.stepHost()).outcome).toBe('completed');
  const recorded = (await host.api.registration()).body as { host_id: string | null };
  expect(recorded.host_id).not.toBeNull();
  const after = (await host.api.health()).body as unknown as Health;
  expect(after).toMatchObject({ status: 'ok', host_id: recorded.host_id });
});

test('test_health_admits_the_hub_origin_cross_origin_and_no_other', async ({ mockHub, runnerHosts }) => {
  const host = await runnerHosts.create();
  const hubOrigin = new URL(mockHub.url).origin;

  const admitted = await host.api.raw.get('/api/health', { headers: { origin: hubOrigin } });
  expect(admitted.status).toBe(200);
  expect(admitted.headers['access-control-allow-origin']).toBe(hubOrigin);
  expect(admitted.headers['vary']).toContain('Origin');

  const foreign = await host.api.raw.get('/api/health', { headers: { origin: 'https://elsewhere.example' } });
  expect(foreign.status).toBe(200);
  expect(foreign.headers['access-control-allow-origin']).toBeUndefined();

  const none = await host.api.raw.get('/api/health');
  expect(none.headers['access-control-allow-origin']).toBeUndefined();
});
