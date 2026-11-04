import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { expect, runnerTest as test } from '../../fixtures';

const at = (reading: unknown, instant: Date): boolean => Math.abs(Date.parse(reading as string) - instant.getTime()) <= 1;

test.describe('a controlled runner-world stack', () => {
  test.use({ clockMode: 'controlled' });

  test('the mock hub and the host each read clock.now() before the body’s first act', async ({ mockHub, runnerHost, clock }) => {
    const now = clock.now();
    expect(at((await mockHub.raw.get('/_clock')).body.now, now)).toBe(true);
    expect(at((await runnerHost.api.raw.get('/api/host/clock')).body.now, now)).toBe(true);
  });

  test('clock.advance moves the mock hub and the host together', async ({ mockHub, runnerHost, clock }) => {
    const moved = await clock.advance(3600);
    expect(at((await mockHub.raw.get('/_clock')).body.now, moved)).toBe(true);
    expect(at((await runnerHost.api.raw.get('/api/host/clock')).body.now, moved)).toBe(true);
    expect(clock.now().getTime()).toBe(moved.getTime());
  });

  test('a mock hub stopped and started after a move rejoins aligned', async ({ mockHub, clock }) => {
    await mockHub.stop();
    const moved = await clock.advance(86_400);
    await mockHub.start();
    expect(at((await mockHub.raw.get('/_clock')).body.now, moved)).toBe(true);
  });

  test('a credential minted after an advance has windows that reset relative to clock.now()', async ({ vendorUsage, worldContext, clock }) => {
    const moved = await clock.advance(10 * 86_400);
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bzh-cred-'));
    try {
      const credential = vendorUsage.mintAnthropic(dir);
      const answer = await worldContext.get(`${vendorUsage.url}/api/oauth/usage`, { headers: { authorization: `Bearer ${credential.token}` }, failOnStatusCode: false });
      expect(answer.status()).toBe(200);
      const body = (await answer.json()) as { five_hour: { resets_at: string }; seven_day: { resets_at: string } };
      expect(Math.abs(Date.parse(body.five_hour.resets_at) - (moved.getTime() + 18_000_000))).toBeLessThan(2_000);
      expect(Math.abs(Date.parse(body.seven_day.resets_at) - (moved.getTime() + 604_800_000))).toBeLessThan(2_000);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
