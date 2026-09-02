import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page, userEvent } from 'vitest/browser';

import { FleetFindingList, type FindingListRowVm } from './finding-list';

/**
 * The design tokens are a global stylesheet (`design/tokens.css`'s own doc comment),
 * loaded via each app's build `styles` — never by a standalone component test
 * (`hover-tint.shell-sweep.spec.ts`'s own note). The gone-row tint claim below is
 * about a resolved `var(--amber)`-derived color, so it reads the sheet's real text
 * (`commands.readFile`) and injects it as a `<style>` element itself, the same way.
 */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

/**
 * The findings triage list's own half of `web:shell-sweep` — a real,
 * headless-Chromium proof of the two classes of claim jsdom cannot make: the
 * bulk bar's own buttons, driven into view exactly the way an operator reaches them
 * (through the select-all checkbox, not by poking component internals), must stay
 * inside the viewport and never overlap each other or the list itself at the phone
 * widths gardening is actually reached at, and a `gone`-flagged row (D8) must carry a
 * genuinely different computed style from a plain row — not merely a different class
 * name jsdom would accept without evaluating it against `finding-list.css`. Gardening
 * sits in the hub's mobile bottom tab bar, so the narrow widths bind
 * (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const ROWS: readonly FindingListRowVm[] = [
  {
    findingId: 'fin_1',
    findingClass: 'style',
    locus: 'a.py:1',
    summary: 'unused import',
    introduced: '2026-01-01T00:00:00Z',
    lastSeenAt: '2026-01-10T00:00:00Z',
    observedCount: 3,
    state: 'live',
    note: null,
    workItem: null,
  },
  {
    findingId: 'fin_2',
    findingClass: 'lint',
    locus: 'b.py:9',
    summary: 'unused variable',
    introduced: '2026-01-02T00:00:00Z',
    lastSeenAt: '2026-01-09T00:00:00Z',
    observedCount: 2,
    state: 'gone',
    note: 'not observed in the last sweep',
    workItem: null,
  },
  {
    findingId: 'fin_3',
    findingClass: 'style',
    locus: 'c.py:4',
    summary: 'missing docstring',
    introduced: null,
    lastSeenAt: '2026-01-08T00:00:00Z',
    observedCount: 1,
    state: 'live',
    note: null,
    workItem: null,
  },
  {
    findingId: 'fin_4',
    findingClass: 'lint',
    locus: 'd.py:12',
    summary: 'unreachable code',
    introduced: '2026-01-03T00:00:00Z',
    lastSeenAt: '2026-01-07T00:00:00Z',
    observedCount: 4,
    state: 'resolved',
    note: 'fixed upstream',
    workItem: null,
  },
];

async function mount() {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [FleetFindingList],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetFindingList);
  fixture.componentRef.setInput('rows', ROWS);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('canControl', true);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  return { fixture, root };
}

describe('FleetFindingList bulk-bar/table shell sweep (web:shell-sweep)', () => {
  it('keeps the bulk bar’s buttons inside the viewport, non-overlapping, and the list itself unclipped at 1400/390/320px', async () => {
    const { fixture, root } = await mount();
    try {
      // Drive selection through the DOM exactly the way an operator would — the
      // select-all header checkbox — rather than poking `FleetFindingList`'s own
      // private selection signal.
      const selectAll = root.querySelector<HTMLInputElement>('[data-testid="gardening-findings-select-all"]')!;
      await userEvent.click(selectAll);
      await fixture.whenStable();

      const bulkBarInitial = root.querySelector<HTMLElement>('[data-testid="gardening-findings-bulk-bar"]');
      expect(bulkBarInitial, 'the bulk bar did not render once every row was selected').not.toBeNull();

      for (const width of [1400, 390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const bulkBar = root.querySelector<HTMLElement>('[data-testid="gardening-findings-bulk-bar"]')!;
        const buttons = Array.from(bulkBar.querySelectorAll<HTMLElement>('button[data-testid^="gardening-finding-bulk-"]'));
        expect(buttons.length, `${width}px: no bulk-bar buttons rendered`).toBeGreaterThan(0);

        for (const button of buttons) {
          const rect = button.getBoundingClientRect();
          expect(
            rect.left,
            `${width}px: bulk-bar button ${button.dataset['testid']} sits left of the viewport`,
          ).toBeGreaterThanOrEqual(0);
          expect(
            rect.right,
            `${width}px: bulk-bar button ${button.dataset['testid']} overflows the ${width}px viewport`,
          ).toBeLessThanOrEqual(width + 1);
        }

        for (let i = 0; i < buttons.length; i++) {
          for (let j = i + 1; j < buttons.length; j++) {
            const a = buttons[i].getBoundingClientRect();
            const b = buttons[j].getBoundingClientRect();
            const overlap = a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
            expect(
              overlap,
              `${width}px: bulk-bar buttons ${buttons[i].dataset['testid']} and ${buttons[j].dataset['testid']} overlap`,
            ).toBe(false);
          }
        }

        expect(
          root.scrollWidth,
          `${width}px: the findings list overflows horizontally (${root.scrollWidth} > ${root.clientWidth})`,
        ).toBeLessThanOrEqual(root.clientWidth);
      }
    } finally {
      root.remove();
    }
  });
});

describe('FleetFindingList gone-row tint shell sweep (web:shell-sweep)', () => {
  it('gives a gone-flagged row (D8) a genuinely different computed style than a plain, untinted row', async () => {
    await loadDesignTokens();
    const { root } = await mount();
    try {
      await page.viewport(1400, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const plain = root.querySelector<HTMLElement>('[data-testid="gardening-finding-row-fin_1"]')!;
      const gone = root.querySelector<HTMLElement>('[data-testid="gardening-finding-row-fin_2"]')!;
      const plainBg = getComputedStyle(plain).backgroundColor;
      const goneBg = getComputedStyle(gone).backgroundColor;
      expect(goneBg, `a gone-flagged row's background (${goneBg}) must not read as a plain row's (${plainBg})`).not.toBe(
        plainBg,
      );

      const plainBorder = getComputedStyle(plain).borderLeftColor;
      const goneBorder = getComputedStyle(gone).borderLeftColor;
      expect(
        goneBorder,
        `a gone-flagged row's border-left (${goneBorder}) must not read as a plain row's (${plainBorder})`,
      ).not.toBe(plainBorder);
    } finally {
      root.remove();
    }
  });
});
