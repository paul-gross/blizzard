import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page } from 'vitest/browser';

import { FleetRunDelta, type RunDeltaVm } from './run-delta';
import { FleetRunList, type RunListRowVm } from './run-list';

/**
 * The design tokens are a global stylesheet (`design/tokens.css`'s own doc comment),
 * loaded via each app's build `styles` — never by a standalone component test
 * (`hover-tint.shell-sweep.spec.ts`'s own note). The escalated-row claim below is about
 * a resolved `var(--red)`-derived color, so it reads the sheet's real text
 * (`commands.readFile`) and injects it as a `<style>` element itself, the same way.
 */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

/**
 * The gardening runs-and-findings tab's own half of `web:shell-sweep` (blizzard#397
 * Phase 3) — a real, headless-Chromium proof of the two classes of layout/style claim
 * jsdom cannot make: {@link FleetRunList}'s escalated row must carry a genuinely
 * different computed `background-color` from a normal row (not merely a different
 * class name jsdom would accept without evaluating it against the stylesheet), and
 * {@link FleetRunDelta}'s finding-set blocks — and, within each set, its own
 * added/observed/gone groups — must genuinely stack with distinct `top`s rather than
 * overlapping at a phone width. Gardening sits in the hub's mobile bottom tab bar, so
 * the narrow widths bind (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const ROWS: readonly RunListRowVm[] = [
  {
    chunkId: 'ch_1',
    routineName: 'nightly',
    scopeSlug: 'blizzard',
    mode: 'full',
    mintedAt: '2026-01-10T00:00:00Z',
    outcome: 'done',
    escalated: false,
    delivered: [],
  },
  {
    chunkId: 'ch_2',
    routineName: 'nightly',
    scopeSlug: 'web',
    mode: 'delta',
    mintedAt: '2026-01-11T00:00:00Z',
    outcome: 'needs_human',
    escalated: true,
    delivered: [],
  },
];

const DELTA_VM: RunDeltaVm = {
  chunkId: 'ch_1',
  routineName: 'nightly',
  scopeSlug: 'blizzard',
  mode: 'full',
  outcome: 'done',
  escalation: null,
  sets: [
    {
      findingSetId: 'fins_1',
      revisionsLabel: 'blizzard@abc123',
      measurement: '3 findings',
      added: [{ findingId: 'fnd_1', findingClass: 'style', locus: 'a.py:1', summary: 'unused import', introduced: null }],
      observed: ['fnd_2'],
      gone: [{ findingId: 'fnd_3', note: 'resolved' }],
    },
    {
      findingSetId: 'fins_2',
      revisionsLabel: 'web@fedcba',
      measurement: null,
      added: [{ findingId: 'fnd_4', findingClass: 'lint', locus: 'b.py:9', summary: 'unused variable', introduced: null }],
      observed: [],
      gone: [],
    },
  ],
};

describe('FleetRunList escalated row shell sweep (web:shell-sweep, blizzard#397 Phase 3)', () => {
  it('gives an escalated row a genuinely different computed background than a normal row', async () => {
    await loadDesignTokens();
    await TestBed.configureTestingModule({
      imports: [FleetRunList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetRunList);
    fixture.componentRef.setInput('rows', ROWS);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    try {
      await page.viewport(390, 800);
      await fixture.whenStable();

      const normal = root.querySelector<HTMLElement>('[data-testid="gardening-run-row-ch_1"]')!;
      const escalated = root.querySelector<HTMLElement>('[data-testid="gardening-run-row-ch_2"]')!;
      const normalBg = getComputedStyle(normal).backgroundColor;
      const escalatedBg = getComputedStyle(escalated).backgroundColor;
      expect(escalatedBg, 'the escalated row must not share the normal row’s computed background').not.toBe(normalBg);

      const normalBorder = getComputedStyle(normal).borderLeftColor;
      const escalatedBorder = getComputedStyle(escalated).borderLeftColor;
      expect(
        escalatedBorder,
        'the escalated row must not share the normal row’s computed border-left color',
      ).not.toBe(normalBorder);
    } finally {
      root.remove();
    }
  });
});

describe('FleetRunDelta stacking shell sweep (web:shell-sweep, blizzard#397 Phase 3)', () => {
  it('stacks every finding set, and each set’s added/observed/gone groups, with no overlap at 390px', async () => {
    await TestBed.configureTestingModule({
      imports: [FleetRunDelta],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetRunDelta);
    fixture.componentRef.setInput('vm', DELTA_VM);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    try {
      await page.viewport(390, 900);
      await fixture.whenStable();

      const set1 = root.querySelector<HTMLElement>('[data-testid="gardening-run-delta-set-fins_1"]')!;
      const set2 = root.querySelector<HTMLElement>('[data-testid="gardening-run-delta-set-fins_2"]')!;
      expect(set2.getBoundingClientRect().top, 'the second finding set did not land below the first').toBeGreaterThanOrEqual(
        set1.getBoundingClientRect().bottom,
      );

      const added = set1.querySelector<HTMLElement>('[data-testid="rd-group-added"]')!;
      const observed = set1.querySelector<HTMLElement>('[data-testid="rd-group-observed"]')!;
      const gone = set1.querySelector<HTMLElement>('[data-testid="rd-group-gone"]')!;
      expect(observed.getBoundingClientRect().top, 'observed did not land below added').toBeGreaterThanOrEqual(
        added.getBoundingClientRect().bottom,
      );
      expect(gone.getBoundingClientRect().top, 'gone did not land below observed').toBeGreaterThanOrEqual(
        observed.getBoundingClientRect().bottom,
      );

      expect(root.scrollWidth, `delta overflows horizontally at 390px (${root.scrollWidth} > ${root.clientWidth})`).toBeLessThanOrEqual(
        root.clientWidth,
      );
    } finally {
      root.remove();
    }
  });
});
