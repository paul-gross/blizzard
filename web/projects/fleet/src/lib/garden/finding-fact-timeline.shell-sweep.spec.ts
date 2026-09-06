import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { FindingFactView } from '../api/hub';
import { FleetFindingFactTimeline } from './finding-fact-timeline';

/**
 * The finding fact timeline (blizzard#487, phase 1 of 2, the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method) — a real,
 * headless-Chromium proof that a long human-authored note genuinely wraps
 * (`overflow-wrap: anywhere`) inside its row rather than forcing the row wider than
 * its column: jsdom lays out flex/block content without ever checking whether a long
 * unbroken run of text actually overflows it. Gardening sits in the hub's mobile
 * bottom tab bar, so the narrow widths (390px, 320px) are load-bearing, not
 * incidental (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const FACTS: FindingFactView[] = [
  { kind: 'add', recorded_at: '2026-01-01T00:00:00Z' },
  {
    kind: 'wont-fix',
    recorded_at: '2026-01-02T00:00:00Z',
    actor: 'u_01ABCDEFGHJKMNPQRSTVWXYZ0123',
    note:
      'This finding was reviewed at length and the team decided the churn of fixing it across every call site was not worth it given the routine sweeps every module regardless, so it is being explicitly closed as will-not-fix rather than left to linger indefinitely in the live bucket.',
  },
];

async function render() {
  await TestBed.configureTestingModule({
    imports: [FleetFindingFactTimeline],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetFindingFactTimeline);
  fixture.componentRef.setInput('facts', FACTS);
  await fixture.whenStable();
  return fixture;
}

describe('finding fact timeline layout shell sweep (web:shell-sweep, blizzard#487)', () => {
  it.each([390, 320])('wraps a long note with no horizontal overflow at %ipx', async (width) => {
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 600);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const rows = root.querySelectorAll('[data-testid="finding-fact-row"]');
      expect(rows.length).toBe(FACTS.length);

      expect(
        root.scrollWidth,
        `${width}px: host overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
      ).toBeLessThanOrEqual(root.clientWidth);
    } finally {
      root.remove();
    }
  });
});
