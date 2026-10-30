import * as fs from 'node:fs';
import * as path from 'node:path';
import { expect, test } from '../../fixtures';

test.describe('on the auth profile', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('init succeeds under the operator’s session, the host keeps none, and the socket steps while TCP is gated', async ({ runnerHost, api }) => {
    const runner = runnerHost.runners['runner-local'];
    const listed = await api.get('/api/runners');
    expect((listed.body.runners as { runner_id: string }[]).map((entry) => entry.runner_id)).toContain(runner?.id);

    // The host's own config home holds no session; the operator's was the config home init ran under.
    expect(fs.existsSync(path.join(runnerHost.home, '.config', 'blizzard', 'sessions.json'))).toBe(false);

    // Over the socket a pass completes; over TCP the step route is gated for want of a session.
    expect((await runnerHost.step()).outcome).toBe('completed');
    const gated = await runnerHost.api.raw.post(`/runners/${runner?.id}/api/ticks`);
    expect([401, 403]).toContain(gated.status);
  });
});

test.describe('an adopted runner', () => {
  test.use({ isolation: 'fresh-tenant', runnerStarted: false });

  test('signs in with its own id while the hub lists no host for it', async ({ api, fleet, federation, runnerHosts }) => {
    const minted = await fleet('adopted-one');
    const host = await runnerHosts.create({ runnerStarted: false, runnerAdopted: { name: 'adopted-one', runnerId: minted.runnerId, token: minted.token } });
    expect(host.runners['adopted-one']?.id).toBe(minted.runnerId);
    expect(host.hubTokens()).toEqual({ host: undefined, runners: { 'adopted-one': minted.token } });
    host.reconfigure({ public_url: host.url });
    await host.restart();
    // A stepped host registers its adopted runner on a pass; the pass answers once the hub has.
    await host.step();

    const bounce = await federation.begin(host.url);
    expect(bounce.client).toBe(minted.runnerId);
    const hosts = await api.get('/api/hosts');
    expect(hosts.status).toBe(200);
    expect(hosts.body).toEqual({ hosts: [] });
  });

  test('is refused a first boot at creation', async ({ fleet, runnerHosts }) => {
    const minted = await fleet('adopted-two');
    await expect(
      runnerHosts.create({ runnerStarted: true, runnerAdopted: { name: 'adopted-two', runnerId: minted.runnerId, token: minted.token } }),
    ).rejects.toThrow(/runnerStarted: false/);
  });
});
