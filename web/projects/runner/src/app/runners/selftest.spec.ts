import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { type runnerApi } from 'fleet';
import { harnessHealthView, type RequestClientStub, runnerDashboardView, selfTestView, settle, stubError, stubRunnerClient } from 'fleet/testing';
import { vi } from 'vitest';

import { LocalSelftest } from './selftest';
import { SELFTEST_POLL_MS } from './selftest.query';

const DASHBOARD = runnerDashboardView({
  harness_health: { items: [harnessHealthView({ harness_id: 'mock' }), harnessHealthView({ harness_id: 'claude_code' })] },
});
const CHECKS: runnerApi.SelfTestCheckView[] = [
  { name: 'spawn', passed: true, detail: 'spawned' },
  { name: 'resume', passed: false, detail: 'no session' },
];

/** Change detection over a few macrotask turns, without waiting for stability — a start held
 * open by the test keeps the app unstable. */
async function turn(fixture: { detectChanges(): void }): Promise<void> {
  for (let i = 0; i < 8; i += 1) {
    fixture.detectChanges();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

let stub: RequestClientStub;
afterEach(() => stub.restore());

async function mount(routes: Parameters<typeof stubRunnerClient>[0]) {
  stub = stubRunnerClient({ 'GET /runners/{runner_id}/api/dashboard': () => DASHBOARD, ...routes });
  await TestBed.configureTestingModule({
    imports: [LocalSelftest],
    providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalSelftest);
  fixture.componentRef.setInput('runnerId', 'rn_1');
  await settle(fixture);
  const el = fixture.nativeElement as HTMLElement;
  const button = (harness: string) => el.querySelector<HTMLButtonElement>(`button[data-testid="run-selftest-${harness}"]`)!;
  return { fixture, el, button };
}

describe('LocalSelftest', () => {
  it('starts a run, polls it until terminal, renders its checks and error, and re-reads the dashboard', async () => {
    let reads = 0;
    const { fixture, el, button } = await mount({
      'POST /runners/{runner_id}/api/selftests': () => selfTestView(),
      'GET /runners/{runner_id}/api/selftests/{selftest_id}': () => {
        reads += 1;
        return reads < 2 ? selfTestView() : selfTestView({ status: 'failed', settled: true, checks: CHECKS, error: 'resume failed' });
      },
    });
    expect(el.querySelectorAll('[data-testid="selftest"]')).toHaveLength(2);

    button('mock').click();
    await settle(fixture);
    expect(stub.forRoute('/runners/rn_1/api/selftests', 'POST')[0].body).toEqual({ harness: 'mock' });
    expect(button('mock').disabled).toBe(true);
    expect(button('claude_code').disabled).toBe(false);
    const dashboardReads = stub.forRoute('/runners/rn_1/api/dashboard', 'GET').length;

    await vi.waitFor(async () => {
      await settle(fixture);
      expect(el.querySelector('[data-testid="selftest-status"]')?.textContent?.trim()).toBe('failed');
    }, { timeout: SELFTEST_POLL_MS * 4 });
    expect(reads).toBe(2);
    const checks = [...el.querySelectorAll('[data-testid="selftest-check"]')].map((check) => [...check.children].map((part) => part.textContent?.trim()).join(' '));
    expect(checks).toEqual(['PASS spawn spawned', 'FAIL resume no session']);
    expect(el.querySelector('[data-testid="selftest-run-error"]')?.textContent).toContain('resume failed');
    expect(button('mock').disabled).toBe(false);
    await vi.waitFor(() => expect(stub.forRoute('/runners/rn_1/api/dashboard', 'GET').length).toBeGreaterThan(dashboardReads));

    await new Promise((resolve) => setTimeout(resolve, SELFTEST_POLL_MS * 1.5));
    expect(reads).toBe(2);
  });

  it('renders a refused start on that harness alone', async () => {
    const { fixture, el, button } = await mount({
      'POST /runners/{runner_id}/api/selftests': () => stubError(503, { detail: 'harness mock is unavailable' }),
    });

    button('mock').click();
    await settle(fixture);

    const rows = el.querySelectorAll('[data-testid="selftest"]');
    expect(rows[0].querySelector('[data-testid="selftest-error"]')?.textContent).toContain('harness mock is unavailable');
    expect(rows[1].querySelector('[data-testid="selftest-error"]')?.textContent ?? '').not.toContain('unavailable');
    expect(button('mock').disabled).toBe(false);
  });

  describe('two overlapping starts', () => {
    type Outcome = 'ok' | 'err';
    const cases: [string, Outcome, Outcome, 'mock' | 'claude_code'][] = [
      ['both succeed, first answers first', 'ok', 'ok', 'mock'],
      ['both succeed, second answers first', 'ok', 'ok', 'claude_code'],
      ['first fails, second succeeds, first answers first', 'err', 'ok', 'mock'],
      ['first fails, second succeeds, second answers first', 'err', 'ok', 'claude_code'],
      ['first succeeds, second fails, first answers first', 'ok', 'err', 'mock'],
      ['first succeeds, second fails, second answers first', 'ok', 'err', 'claude_code'],
    ];

    it.each(cases)('%s — each harness keeps its own pending state, run and error', async (_name, mockOutcome, claudeOutcome, firstAnswer) => {
      const outcome: Record<string, Outcome> = { mock: mockOutcome, claude_code: claudeOutcome };
      const release: Record<string, () => void> = {};
      const { fixture, el, button } = await mount({
        'POST /runners/{runner_id}/api/selftests': async (req) => {
          const harness = (req.body as { harness: string }).harness;
          await new Promise<void>((resolve) => (release[harness] = resolve));
          return outcome[harness] === 'ok'
            ? selfTestView({ id: `st_${harness}`, harness })
            : stubError(503, { detail: `harness ${harness} is unavailable` });
        },
        'GET /runners/{runner_id}/api/selftests/{selftest_id}': (_req, { selftest_id }) =>
          selfTestView({ id: selftest_id, harness: selftest_id.replace(/^st_/, '') }),
      });
      const row = (harness: string) => el.querySelector(`[data-testid="selftest"][data-harness-id="${harness}"]`)!;

      button('mock').click();
      await turn(fixture);
      button('claude_code').click();
      await turn(fixture);
      expect(button('mock').disabled).toBe(true);
      expect(button('claude_code').disabled).toBe(true);

      const secondAnswer = firstAnswer === 'mock' ? 'claude_code' : 'mock';
      release[firstAnswer]();
      await turn(fixture);
      expect(button(secondAnswer).disabled).toBe(true);
      expect(button(firstAnswer).disabled).toBe(outcome[firstAnswer] === 'ok');
      release[secondAnswer]();
      await turn(fixture);

      for (const harness of ['mock', 'claude_code']) {
        const error = row(harness).querySelector('[data-testid="selftest-error"]')?.textContent ?? '';
        if (outcome[harness] === 'ok') {
          expect(row(harness).querySelector('[data-testid="selftest-status"]')?.textContent?.trim()).toBe('running');
          expect(button(harness).disabled).toBe(true);
          expect(error).not.toContain('unavailable');
        } else {
          expect(row(harness).querySelector('[data-testid="selftest-status"]')).toBeNull();
          expect(button(harness).disabled).toBe(false);
          expect(error).toContain(`harness ${harness} is unavailable`);
        }
      }
    });
  });
});
