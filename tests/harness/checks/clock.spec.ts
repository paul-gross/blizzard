import { test as plain } from '@playwright/test';
import { checkRunnerEnv } from '../runner-host';
import { expect, test } from '../fixtures';
import { requireClockOptions, requireHubOptions, requireSteppedHub } from '../tenancy';

// Both the hub's reading and the stack's are exact to the millisecond once a move has set them; a daemon that was the one
// ahead at alignment holds microseconds the stack's record rounded up over.
const at = (reading: unknown, instant: Date): boolean => Math.abs(Date.parse(reading as string) - instant.getTime()) <= 1;

// The wiring checks hold under BLIZZARD_ISOLATION_RAISE: the served level is the key, so `fresh-tenant` is served
// `fresh-hub` there and a controlled stack is admitted.
const raised = process.env.BLIZZARD_ISOLATION_RAISE === '1';

test.describe('a controlled stack at fresh-hub, with a runner host and a mock runner', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled' });

  test('the hub, the mock forge, the host and the mock runner each read clock.now() before the body’s first act', async ({ api, forge, runnerHost, mockRunner, clock }) => {
    const runner = await mockRunner('clock-runner');
    const now = clock.now();
    expect(at((await api.get('/api/admin/clock')).body.now, now)).toBe(true);
    expect(at((await forge.call('GET', '/_clock')).body.now, now)).toBe(true);
    expect(at((await runnerHost.api.raw.get('/api/host/clock')).body.now, now)).toBe(true);
    expect(at((await runner.clock()).body.now, now)).toBe(true);
  });

  test('clock.advance moves every clock and the page, and resolves with the new instant', async ({ api, forge, runnerHost, mockRunner, clock, page }) => {
    const runner = await mockRunner('clock-runner');
    expect(await page.evaluate(() => Date.now())).toBe(clock.now().getTime());
    const before = clock.now();
    const moved = await clock.advance(3600);
    expect(moved.getTime()).toBe(before.getTime() + 3_600_000);
    expect(clock.now().getTime()).toBe(moved.getTime());
    expect(at((await api.get('/api/admin/clock')).body.now, moved)).toBe(true);
    expect(at((await forge.call('GET', '/_clock')).body.now, moved)).toBe(true);
    expect(at((await runnerHost.api.raw.get('/api/host/clock')).body.now, moved)).toBe(true);
    expect(at((await runner.clock()).body.now, moved)).toBe(true);
    expect(await page.evaluate(() => Date.now())).toBe(moved.getTime());
  });

  test('clock.set moves to an instant, and refuses one before the stack’s own', async ({ api, clock }) => {
    const target = new Date(clock.now().getTime() + 5_000);
    expect((await clock.set(target)).getTime()).toBe(target.getTime());
    expect(at((await api.get('/api/admin/clock')).body.now, target)).toBe(true);
    await expect(clock.set(new Date(target.getTime() - 1))).rejects.toThrow(/before the stack/);
    // Refused before any request: the hub still reads the instant it was moved to.
    expect(at((await api.get('/api/admin/clock')).body.now, target)).toBe(true);
    expect(clock.now().getTime()).toBe(target.getTime());
  });

  test('api.flowStep() on the controlled hub answers with now equal to clock.now()', async ({ api, clock }) => {
    await clock.advance(120);
    const step = await api.flowStep();
    expect(at(step.now, clock.now())).toBe(true);
  });

  test('a host made mid-test reads the stack’s instant on joining, behind or not', async ({ runnerHosts, clock }) => {
    await clock.advance(7200);
    const host = await runnerHosts.create();
    expect(at((await host.api.raw.get('/api/host/clock')).body.now, clock.now())).toBe(true);
  });

  test('a fresh hub stopped and started after a day’s advance reads clock.now()', async ({ api, freshHub, clock }) => {
    await freshHub.stop();
    const after = await clock.advance(86_400);
    await freshHub.start();
    expect(at((await api.get('/api/admin/clock')).body.now, after)).toBe(true);
    expect(at((await api.flowStep()).now, clock.now())).toBe(true);
  });

  test('a host restarted after a move rejoins aligned', async ({ runnerHost, clock }) => {
    await runnerHost.stop();
    await clock.advance(600);
    await runnerHost.restart();
    expect(at((await runnerHost.api.raw.get('/api/host/clock')).body.now, clock.now())).toBe(true);
  });
});

test.describe('a system stack', () => {
  test.use({ isolation: 'common' });

  test('clock.now() is the machine’s time, and advance and set are refused naming clockMode', async ({ clock }) => {
    expect(Math.abs(clock.now().getTime() - Date.now())).toBeLessThan(5_000);
    await expect(clock.advance(1)).rejects.toThrow(/clockMode/);
    await expect(clock.set(new Date())).rejects.toThrow(/clockMode/);
    expect(() => clock.suspendSessionRefresh()).not.toThrow();
  });
});

test.describe('controlled below fresh-hub', () => {
  test.use({ isolation: 'fresh-tenant', clockMode: 'controlled' });
  test.fail(!raised, 'a controlled stack needs fresh-hub');
  test('is refused before the hub starts', ({ level }) => {
    expect(level.served).toBe('fresh-hub');
  });
});

test.describe('a timed host on a controlled stack', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled', runnerLoop: 'timed' });
  test.fail(true, 'a controlled stack pairs only with stepped daemons');
  test('is refused', async ({ runnerHost }) => {
    void runnerHost;
  });
});

test.describe('a timed sweep in hubConfig on a controlled stack', () => {
  test.use({ isolation: 'fresh-hub', clockMode: 'controlled', hubConfig: { sweeps: { trace_export: { mode: 'timed' } } } });
  test.fail(true, 'a controlled stack pairs only with stepped daemons');
  test('is refused', ({ testHub }) => {
    void testHub;
  });
});

test.describe('BLIZZARD_CLOCK in hubEnv', () => {
  test.use({ isolation: 'fresh-hub', hubEnv: { BLIZZARD_CLOCK: 'controlled' } });
  test.fail(true, 'the fixture sets BLIZZARD_CLOCK');
  test('is refused', ({ level }) => {
    void level;
  });
});

test.describe('flowStepped beside clockMode controlled', () => {
  test.use({ isolation: 'fresh-hub', flowStepped: true, clockMode: 'controlled' });
  test.fail(true, 'flowStepped and clockMode controlled exclude each other');
  test('is refused', ({ level }) => {
    void level;
  });
});

plain.describe('the refusals name the key or option, and refuse before any daemon starts', () => {
  plain('a controlled stack below fresh-hub names fresh-hub', () => {
    expect(() => requireClockOptions('controlled', 'fresh-tenant', false)).toThrow(/clockMode.*fresh-hub/);
    expect(() => requireClockOptions('controlled', 'fresh-hub', false)).not.toThrow();
    expect(() => requireClockOptions('system', 'common', true)).not.toThrow();
  });

  plain('flowStepped beside clockMode names both options', () => {
    expect(() => requireClockOptions('controlled', 'fresh-hub', true)).toThrow(/flowStepped.*clockMode/);
  });

  plain('a timed sweep in the merged profile is named by its key', () => {
    expect(() => requireSteppedHub({ sweeps: { trace_export: { mode: 'timed' } } }, 'the profile')).toThrow(/sweeps\.trace_export\.mode/);
  });

  plain('BLIZZARD_CLOCK in hubEnv or runnerEnv is refused', () => {
    expect(() => requireHubOptions('fresh-hub', {}, { BLIZZARD_CLOCK: 'controlled' })).toThrow(/hubEnv BLIZZARD_CLOCK/);
    expect(checkRunnerEnv({ BLIZZARD_CLOCK: 'controlled' }).join('\n')).toMatch(/runnerEnv BLIZZARD_CLOCK/);
  });
});
