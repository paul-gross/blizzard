import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { RunnerRow } from './runner-panel';
import { RunnerPanelView } from './runner-view';

/**
 * The runner registry's rate-limit pace bars (issue #218), the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method — a real,
 * headless-Chromium proof that the stacked utilization/elapsed pair genuinely stacks
 * (two distinct rows, not overlapping) and stays within the fleet panel's own width at
 * the board's right-rail viewport, ~390px. jsdom lays out flex children without ever
 * checking whether they actually clip, so this is exactly the class of layout claim
 * `web:unit-test` cannot make good on (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const NOW = new Date().toISOString();

const ROW: RunnerRow = {
  runner_id: 'rn_paced',
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  hub_paused: false,
  locally_paused: false,
  claims: [],
  used: 0,
  paceBars: [
    { window: '5h', utilizationPct: 62, elapsedPct: 38 },
    { window: '7d', utilizationPct: 81, elapsedPct: 90 },
  ],
};

async function render() {
  await TestBed.configureTestingModule({
    imports: [RunnerPanelView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(RunnerPanelView);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('rows', [ROW]);
  await fixture.whenStable();
  return fixture;
}

describe('runner registry pace bars layout shell sweep (web:shell-sweep, blizzard#218)', () => {
  it('stacks the utilization and elapsed bars for both windows with no horizontal overflow at ~390px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const panel = root.querySelector<HTMLElement>('[data-testid="runner-panel"]')!;
      expect(panel).not.toBeNull();

      const bars = root.querySelectorAll<HTMLElement>('[data-runner-pace-bar="rn_paced"]');
      expect(bars).toHaveLength(2);

      // The two windows' bars sit on distinct rows, not overlapping — a genuine flex
      // stack, not a collapsed one.
      const tops = [...bars].map((b) => b.getBoundingClientRect().top);
      expect(new Set(tops).size, `pace bars did not stack — tops were ${tops.join(', ')}`).toBe(2);

      // Within each bar, its own utilization/elapsed pair also stacks — the utilization
      // row above the elapsed row.
      for (const bar of bars) {
        const util = bar.querySelector<HTMLElement>('[data-testid="pace-bar-utilization"]')!;
        const elapsed = bar.querySelector<HTMLElement>('[data-testid="pace-bar-elapsed"]')!;
        expect(util.getBoundingClientRect().top).toBeLessThan(elapsed.getBoundingClientRect().top);
      }

      // Nothing pushes the panel wider than its own box — the pace bars fit the rail
      // rather than clipping or forcing horizontal scroll.
      expect(
        panel.scrollWidth,
        `panel overflows horizontally at 390px (${panel.scrollWidth} > ${panel.clientWidth})`,
      ).toBeLessThanOrEqual(panel.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
