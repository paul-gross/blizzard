import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';
import type { RunnerRow } from 'fleet';

import { FleetView } from './fleet-view';

/**
 * The mobile Fleet screen's runner cards (the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method) — a real,
 * headless-Chromium proof that each runner card genuinely stacks below the last with
 * no horizontal overflow at phone widths, and that a card carrying claims, a slot bar,
 * and grouped subscription pace bars stays inside its own width even with a long
 * chunk id and a long subscription name. jsdom lays out this flex column without ever
 * checking whether the pace bars or the claim line actually clip, so this is exactly
 * the class of layout claim `web:unit-test` cannot make good on
 * (`bzh:narrow-viewport-tier-rule`) — Fleet sits in the hub's mobile bottom tab bar,
 * so the narrow widths are load-bearing, not incidental.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const NOW = new Date().toISOString();

const ROWS: readonly RunnerRow[] = [
  {
    runner_id: 'rn_online',
    workspace_id: 'ws_a',
    registered_at: NOW,
    last_seen_at: NOW,
    online: true,
    hub_paused: false,
    locally_paused: false,
    env_capacity: 4,
    used: 2,
    claims: [
      { chunkId: 'ch_01claim000000000000000000000', shortId: 'C-01CLAIM0000000000000000', node: 'build', status: 'running' },
    ],
    subscriptionPaces: [
      {
        slug: 'anthropic-default',
        name: 'Anthropic (default) — a genuinely long subscription display name',
        paceBars: [
          { window: '5h', utilizationPct: 40, elapsedPct: 20 },
          { window: '7d', utilizationPct: 70, elapsedPct: 55 },
        ],
      },
    ],
  },
  {
    runner_id: 'rn_paused',
    workspace_id: 'ws_a',
    registered_at: NOW,
    last_seen_at: NOW,
    online: false,
    hub_paused: true,
    locally_paused: true,
    locally_paused_reason: 'spend ceiling $5.00 reached over the trailing 24h (spend $7.00)',
    used: 0,
    claims: [],
    subscriptionPaces: [{ slug: 'anthropic-default', name: 'Anthropic (default)', paceBars: [] }],
  },
];

async function render() {
  await TestBed.configureTestingModule({
    imports: [FleetView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetView);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('rows', ROWS);
  fixture.componentRef.setInput('canPause', true);
  await fixture.whenStable();
  return fixture;
}

describe('mobile Fleet screen layout shell sweep (web:shell-sweep)', () => {
  it.each([390, 320])('stacks every runner card with no horizontal overflow at %ipx', async (width) => {
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
      await page.viewport(width, 900);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const panel = root.querySelector<HTMLElement>('[data-testid="mobile-fleet-panel"]')!;
      expect(panel).not.toBeNull();

      const cards = Array.from(root.querySelectorAll<HTMLElement>('[data-testid="mobile-fleet-runner"]'));
      expect(cards).toHaveLength(2);

      const rects = cards.map((c) => c.getBoundingClientRect());
      for (let i = 1; i < rects.length; i++) {
        expect(
          rects[i].top,
          `runner card ${i} overlaps the previous one — top ${rects[i].top} < previous bottom ${rects[i - 1].bottom}`,
        ).toBeGreaterThanOrEqual(rects[i - 1].bottom);
      }

      for (const card of cards) {
        expect(
          card.scrollWidth,
          `runner card overflows horizontally at ${width}px (${card.scrollWidth} > ${card.clientWidth})`,
        ).toBeLessThanOrEqual(card.clientWidth);
      }

      // The claim line's long chunk id and the subscription group's long name are the
      // two spans most likely to force a card wider than its own box.
      const claim = root.querySelector<HTMLElement>('[data-runner="rn_online"] [data-testid="mobile-fleet-runner-claim"]')!;
      expect(claim.getBoundingClientRect().right).toBeLessThanOrEqual(cards[0].getBoundingClientRect().right + 1);

      const subName = root.querySelector<HTMLElement>('[data-testid="mobile-fleet-runner-subscription-name"]')!;
      expect(subName.getBoundingClientRect().right).toBeLessThanOrEqual(cards[0].getBoundingClientRect().right + 1);

      const report = root.querySelector<HTMLElement>('[data-testid="mobile-fleet-runner-subscription-unsampled"]')!;
      expect(report.textContent?.trim()).toBe('NO USAGE WINDOWS REPORTED');
      expect(report.getAttribute('aria-label')).toBe('Anthropic (default) sample reported no usage windows');

      expect(
        panel.scrollWidth,
        `panel overflows horizontally at ${width}px (${panel.scrollWidth} > ${panel.clientWidth})`,
      ).toBeLessThanOrEqual(panel.clientWidth);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
