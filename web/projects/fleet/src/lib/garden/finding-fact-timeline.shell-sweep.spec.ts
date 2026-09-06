import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import { FleetFindingPanel, type FindingPanelVm } from './finding-panel';

/**
 * The finding fact timeline (blizzard#487, phase 1 of 2, the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method) — a real,
 * headless-Chromium proof that a long, genuinely unbroken note wraps
 * (`overflow-wrap: anywhere`) inside its row rather than forcing the row wider than
 * its column: jsdom lays out flex/block content without ever checking whether a long
 * unbroken run of text actually overflows it. Gardening sits in the hub's mobile
 * bottom tab bar, so the narrow widths (390px, 320px) are load-bearing, not
 * incidental (`bzh:narrow-viewport-tier-rule`).
 *
 * Mounts the composed `FleetFindingPanel` (review:F11), not the standalone
 * `FleetFindingFactTimeline` this file mounted before: the timeline is never
 * reached on its own in the real app, always inside the panel's own `.fp-timeline`
 * section, and mounting it bare left that section's new `<h4>` heading with no
 * matching CSS rule (F11's own gap) undetected. A typography-only regression isn't
 * something a scrollWidth assertion can catch either way — the value of mounting
 * through the real composition instead is that any *future* layout regression in
 * the composed panel (e.g. the timeline overflowing once real panel chrome
 * constrains its width) is caught where it would actually happen, rather than in
 * an isolation the timeline is never rendered in on its own.
 *
 * The fixture note (review:F3) is a genuinely unbroken 96-character run with no
 * spaces — the previous fixture was ordinary space-separated prose, which wraps at
 * word boundaries with or without `overflow-wrap: anywhere`, so it proved nothing
 * about the rule it claimed to. Proven able to fail: temporarily commenting out
 * `overflow-wrap: anywhere` on `.note`/`.actor` in `finding-fact-timeline.css`
 * fails this spec; restoring it passes again (`chunk-page-layout.shell-sweep.spec.ts`'s
 * own "proven able to fail" convention,
 * `blizzard-context:verification/blizzard/commands/web/shell-sweep.md`).
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const LONG_UNBROKEN_NOTE = '4ba7ef06d9'.repeat(8) + 'abcdef0123456789';

const VM: FindingPanelVm = {
  findingId: 'fin_01M1KANH0RZEABSD44RCEH6G9B',
  findingClass: 'stale-docstring',
  locus: 'src/a.py:1',
  state: 'wont-fix',
  observedCount: 3,
  introducedRev: '4ba7ef06d',
  introducedAt: null,
  firstObservedAt: '2026-01-01T00:00:00Z',
  lastSeenAt: '2026-01-05T00:00:00Z',
  summary: 'docstring narrates a removed parameter',
  note: null,
  facts: [
    { kind: 'add', recorded_at: '2026-01-01T00:00:00Z' },
    {
      kind: 'wont-fix',
      recorded_at: '2026-01-02T00:00:00Z',
      actor: 'u_01ABCDEFGHJKMNPQRSTVWXYZ0123',
      note: LONG_UNBROKEN_NOTE,
    },
  ],
  workItem: null,
};

async function render() {
  await TestBed.configureTestingModule({
    imports: [FleetFindingPanel],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetFindingPanel);
  fixture.componentRef.setInput('vm', VM);
  fixture.componentRef.setInput('state', 'ready');
  await fixture.whenStable();
  return fixture;
}

describe('finding fact timeline layout shell sweep (web:shell-sweep, blizzard#487)', () => {
  it.each([390, 320])('wraps a long unbroken note with no horizontal overflow at %ipx', async (width) => {
    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 600);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      expect(root.querySelector('[data-testid="gardening-finding-panel"]')).not.toBeNull();
      expect(root.querySelector('.fp-timeline')).not.toBeNull();

      const rows = root.querySelectorAll('[data-testid="finding-fact-row"]');
      expect(rows.length).toBe(VM.facts.length);

      expect(
        root.scrollWidth,
        `${width}px: host overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
      ).toBeLessThanOrEqual(root.clientWidth);
    } finally {
      root.remove();
    }
  });
});
