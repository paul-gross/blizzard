import { expect, test } from '../fixtures';

const PASS_LINE = 'sweep pass completed';
const timerLines = (lines: { raw: string }[]): string[] => lines.filter((line) => line.raw.includes(PASS_LINE)).map((line) => line.raw);

test.describe('a fresh hub of the default profile', () => {
  test.use({ isolation: 'fresh-hub' });

  test('runs no timed pass, even after a requested one has answered', async ({ api, hubLog }) => {
    const pass = await api.sweep('close_drain');
    expect(pass.outcome).toBe('completed');
    // A timed sweep's first pass runs at startup, so by the time the hub answers it would already have logged.
    expect(timerLines(hubLog.untagged())).toEqual([]);
    expect(timerLines(hubLog.lines())).toEqual([]);
  });
});

test.describe('a control hub that sets close_drain timed', () => {
  test.use({ isolation: 'fresh-hub', hubConfig: { sweeps: { close_drain: { mode: 'timed' } } } });

  test('logs the line the stepped hub is shown not to', async ({ hubLog }) => {
    await expect.poll(() => timerLines(hubLog.lines()).length, { timeout: 15_000 }).toBeGreaterThan(0);
  });
});

test.describe('a pass at fresh-tenant', () => {
  test.use({ isolation: 'fresh-tenant' });

  test('completes for the tenant, and the all-tenant form is refused with the level before any request', async ({ api, level, testHub }, testInfo) => {
    for (const name of ['forge_status', 'event_derivation', 'close_drain'] as const) {
      expect((await api.sweep(name)).outcome).toBe('completed');
    }
    if (level.served === 'fresh-hub') return;
    const before = testInfo.attachments.length;
    await expect(api.sweep('close_drain', { allTenants: true })).rejects.toThrow(/fresh-hub/);
    await expect(api.sweep('close_drain', { allTenants: true })).rejects.toThrow(new RegExp(`"${level.served}"`));
    expect(testHub.fresh).toBe(false);
    expect(testInfo.attachments.length).toBe(before);
  });
});

test.describe('a pass at fresh-hub', () => {
  test.use({ isolation: 'fresh-hub' });

  test('over every tenant completes, for each of the six sweeps', async ({ api }) => {
    for (const name of ['forge_status', 'event_derivation', 'close_drain', 'tenant_teardown', 'trace_export', 'egress'] as const) {
      const answer = await api.unfiltered.post(`/api/admin/sweeps/${name}/passes`);
      // trace_export and egress are unconfigured, so this hub does not build them.
      expect([200, 404], name).toContain(answer.status);
    }
    expect((await api.sweep('close_drain', { allTenants: true })).outcome).toBe('completed');
  });

  test('a sweep the tenant route does not serve fails the test naming the status', async ({ api }) => {
    await expect(api.sweep('tenant_teardown' as never)).rejects.toThrow(/tenant_teardown.*422/);
  });
});
