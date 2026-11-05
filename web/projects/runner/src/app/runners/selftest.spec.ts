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
        return reads < 2 ? selfTestView() : selfTestView({ status: 'failed', checks: CHECKS, error: 'resume failed' });
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
    const checks = [...el.querySelectorAll('[data-testid="selftest-check"]')].map((check) => [...check.querySelectorAll('span')].map((part) => part.textContent?.trim()).join(' '));
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
});
