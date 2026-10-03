import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { runnerApi } from 'fleet';
import { page } from 'vitest/browser';

import { LocalInfoView } from './app-info-view';

/**
 * The runner panel's info section with a runner imposing several gates — the tooled half
 * of `web:shell-sweep`: a real headless-Chromium proof that the Gates fact row stays
 * inside the panel at ~390px rather than forcing horizontal scroll
 * (`bzh:narrow-viewport-tier-rule`). Excluded from the default `ng test` run; run it via
 * `npm run shell-sweep`.
 */
const STATUS: runnerApi.RunnerStatusView = {
  runner_id: 'runner-local',
  workspace_id: 'workspace-local',
  pause: { local: false, hub: false, effective: false },
  capacities: { max_agents: 4, used: 1, free: 3 },
  hub: { endpoint: 'http://127.0.0.1:8421', reachable: true, last_contact_at: '2026-07-16T11:59:30.000Z', buffer_depth: 2 },
  last_tick_at: '2026-07-16T11:59:45.000Z',
  gates: ['build', 'review', 'deliver-to-master', 'a-node-with-a-rather-long-name-to-force-wrapping'],
};

describe('runner info view gates row shell sweep (web:shell-sweep)', () => {
  it('shows the gates row inside the panel with no page errors or horizontal overflow at ~390px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    await TestBed.configureTestingModule({
      imports: [LocalInfoView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(LocalInfoView);
    fixture.componentRef.setInput('view', STATUS);
    fixture.componentRef.setInput('lastFlushLabel', '-30s');
    fixture.componentRef.setInput('lastTickLabel', '-15s');
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const row = root.querySelector<HTMLElement>('[data-testid="hub-gates"]')!;
      expect(row.textContent).toContain('deliver-to-master');
      expect(row.getBoundingClientRect().right).toBeLessThanOrEqual(document.documentElement.clientWidth);
      expect(
        root.scrollWidth,
        `info view overflows horizontally at 390px (${root.scrollWidth} > ${root.clientWidth})`,
      ).toBeLessThanOrEqual(root.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
