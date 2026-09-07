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
  subscriptionPaces: [],
};

// Labels of unequal glyph count, which the fixed label column must absorb. A provider
// that derives its window labels from the lengths it reports can emit these, so the pair
// is representative rather than contrived — and unlike "5h"/"7d" it is not equal-width in
// the panel's monospace face, which is what makes the alignment claim falsifiable.
const UNEQUAL_LABEL_ROW: RunnerRow = {
  runner_id: 'rn_unequal',
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
    { window: '30d', utilizationPct: 81, elapsedPct: 90 },
  ],
  subscriptionPaces: [],
};

const SUBSCRIPTION_ROW: RunnerRow = {
  runner_id: 'rn_subs',
  workspace_id: 'ws_a',
  registered_at: NOW,
  last_seen_at: NOW,
  online: true,
  hub_paused: false,
  locally_paused: false,
  claims: [],
  used: 0,
  paceBars: [],
  // Two declared subscriptions sharing an identical "5h" window label (blizzard#478) —
  // the layout claim this sweep exists to prove is that the two groups stay visually
  // distinct rather than merging into one shared bar list.
  subscriptionPaces: [
    {
      slug: 'anthropic-default',
      name: 'Anthropic (default)',
      paceBars: [
        { window: '5h', utilizationPct: 62, elapsedPct: 38 },
        { window: '7d', utilizationPct: 81, elapsedPct: 90 },
      ],
    },
    {
      slug: 'anthropic-secondary',
      name: 'Anthropic (secondary)',
      paceBars: [{ window: '5h', utilizationPct: 15, elapsedPct: 5 }],
    },
  ],
};

async function render(rows: readonly RunnerRow[] = [ROW]) {
  await TestBed.configureTestingModule({
    imports: [RunnerPanelView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(RunnerPanelView);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('rows', rows);
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

  it('seats every window label in a fixed left column so unequal labels still share one track edge', async () => {
    const fixture = await render([UNEQUAL_LABEL_ROW]);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const bars = [...root.querySelectorAll<HTMLElement>('[data-runner-pace-bar="rn_unequal"]')];
      expect(bars).toHaveLength(2);

      // Measured over the text, not the element: the label box IS the fixed column, so
      // its own width is 28px for every label and could never show the difference.
      const glyphWidths = bars.map((bar) => {
        const label = bar.querySelector<HTMLElement>('[data-testid="pace-bar-label"]')!;
        const range = document.createRange();
        range.selectNodeContents(label);
        return Math.round(range.getBoundingClientRect().width);
      });

      for (const bar of bars) {
        const label = bar.querySelector<HTMLElement>('[data-testid="pace-bar-label"]')!;
        const util = bar.querySelector<HTMLElement>('[data-testid="pace-bar-utilization"]')!;
        const labelBox = label.getBoundingClientRect();
        const utilBox = util.getBoundingClientRect();

        // Beside the track, not above it: the label's right edge clears the track's left
        // edge, and the two share vertical space rather than stacking.
        expect(labelBox.right).toBeLessThanOrEqual(utilBox.left);
        expect(labelBox.top).toBeLessThan(utilBox.bottom);
      }

      // The fixture's whole purpose: the panel renders monospace, so the alignment claim
      // is only falsifiable with labels whose text genuinely differs in width. If these
      // two ever measure equal, the assertion below stops proving anything.
      expect(new Set(glyphWidths).size, `fixture label text rendered equally wide — ${glyphWidths.join(', ')}`).toBe(2);

      // Unequal labels, one track edge. Under a content-sized column the wider label
      // would push its own track right, and this set would hold two values.
      const trackLefts = bars.map(
        (bar) => bar.querySelector<HTMLElement>('[data-testid="pace-bar-utilization"]')!.getBoundingClientRect().left,
      );
      expect(new Set(trackLefts).size, `bar tracks did not align — lefts were ${trackLefts.join(', ')}`).toBe(1);
    } finally {
      root.remove();
    }
  });

  it('keeps two subscriptions with an identical window label visually distinct, with no page errors or horizontal overflow at ~390px (blizzard#478)', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const fixture = await render([SUBSCRIPTION_ROW]);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(390, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const panel = root.querySelector<HTMLElement>('[data-testid="runner-panel"]')!;
      expect(panel).not.toBeNull();

      const groups = root.querySelectorAll<HTMLElement>('[data-testid="subscription-pace-group"]');
      expect(groups).toHaveLength(2);

      const defaultGroup = root.querySelector<HTMLElement>('[data-subscription-slug="anthropic-default"]')!;
      const secondaryGroup = root.querySelector<HTMLElement>('[data-subscription-slug="anthropic-secondary"]')!;

      // The two groups sit on distinct rows, not overlapping — a genuine stack, not a
      // collapsed one.
      expect(defaultGroup.getBoundingClientRect().top).toBeLessThan(secondaryGroup.getBoundingClientRect().top);

      // Each group's own "5h" window stays scoped to it — the identical label never
      // merges the two subscriptions' bars into one.
      expect(defaultGroup.querySelectorAll('[data-pace-window="5h"]')).toHaveLength(1);
      expect(secondaryGroup.querySelectorAll('[data-pace-window="5h"]')).toHaveLength(1);

      // Nothing pushes the panel wider than its own box at the narrow, mobile-reachable
      // rail width.
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
