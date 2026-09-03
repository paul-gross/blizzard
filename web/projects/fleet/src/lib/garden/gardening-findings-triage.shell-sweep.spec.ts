import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

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
 * headless-Chromium proof of the three classes of claim jsdom cannot make: the
 * list itself must stay unclipped at the phone widths gardening is actually
 * reached at (`bzh:narrow-viewport-tier-rule`); a `gone`-flagged row (D8) must
 * carry a genuinely different computed style from a plain row — not merely a
 * different class name jsdom would accept without evaluating it against
 * `finding-list.css`; and the row's own summary headline must genuinely clamp to
 * four lines rather than merely carrying the `-webkit-line-clamp` declaration —
 * jsdom performs no layout, so it cannot tell a clamped headline from an
 * unclamped one.
 *
 * Multi-select and the bulk action bar were removed from `FleetFindingList`:
 * triage now dispatches one finding at a time, from `fleet-finding-panel`'s own
 * `triage` output, opened by a row click. This file's own bulk-bar-in-viewport
 * sweep went with it — there is nothing left of that concern to sweep. A later
 * `shell-sweep` pass owns deciding whether a narrow-viewport claim belongs on the
 * finding panel's own triage affordance instead; this file stays scoped to
 * `FleetFindingList`.
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
    state: 'live',
    lastSeenAt: '2026-01-05T00:00:00Z',
  },
  {
    findingId: 'fin_2',
    findingClass: 'lint',
    locus: 'b.py:9',
    summary: 'unused variable',
    state: 'gone',
    lastSeenAt: '2026-01-06T00:00:00Z',
  },
  {
    findingId: 'fin_3',
    findingClass: 'style',
    locus: 'c.py:4',
    summary: 'missing docstring',
    state: 'live',
    lastSeenAt: '2026-01-04T00:00:00Z',
  },
  {
    findingId: 'fin_4',
    findingClass: 'lint',
    locus: 'd.py:12',
    summary: 'unreachable code',
    state: 'resolved',
    lastSeenAt: null,
  },
];

async function mount(rows: readonly FindingListRowVm[] = ROWS) {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [FleetFindingList],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetFindingList);
  fixture.componentRef.setInput('rows', rows);
  fixture.componentRef.setInput('state', 'ready');
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  return { fixture, root };
}

describe('FleetFindingList list-layout shell sweep (web:shell-sweep)', () => {
  it('keeps the list itself unclipped at 1400/390/320px', async () => {
    const { root } = await mount();
    try {
      for (const width of [1400, 390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

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
  it('gives a gone-flagged row’s body a genuinely different computed background than a plain row’s, leaving the selection edge alone', async () => {
    await loadDesignTokens();
    const { root } = await mount();
    try {
      await page.viewport(1400, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      // The gone tint rides `.fl-body`, the projected content inside
      // `fleet-kit-select-row`'s own encapsulated `<button>` — `run-list.ts`'s own
      // `.rl-body` shape, compared here where each background is actually drawn
      // rather than on the outer button (`kit-select-row.css`'s own `.selected`).
      const plainBody = root.querySelector<HTMLElement>('[data-testid="gardening-finding-row-fin_1"] .fl-body')!;
      const goneBody = root.querySelector<HTMLElement>('[data-testid="gardening-finding-row-fin_2"] .fl-body')!;
      const plainBg = getComputedStyle(plainBody).backgroundColor;
      const goneBg = getComputedStyle(goneBody).backgroundColor;
      expect(goneBg, `a gone-flagged row's background (${goneBg}) must not read as a plain row's (${plainBg})`).not.toBe(
        plainBg,
      );

      // The left edge belongs to selection unconditionally — an unselected
      // gone-flagged row must not claim it.
      const plainRow = root.querySelector<HTMLElement>('[data-testid="gardening-finding-row-fin_1"]')!;
      const goneRow = root.querySelector<HTMLElement>('[data-testid="gardening-finding-row-fin_2"]')!;
      expect(
        getComputedStyle(goneRow).borderLeftColor,
        'an unselected gone-flagged row must not claim the selection edge',
      ).toBe(getComputedStyle(plainRow).borderLeftColor);
    } finally {
      root.remove();
    }
  });
});

describe('FleetFindingList summary headline clamp shell sweep (web:shell-sweep)', () => {
  it('genuinely clamps a long summary to four lines rather than letting it grow the row', async () => {
    const LONG_SUMMARY = Array.from(
      { length: 20 },
      (_, i) => `Sentence number ${i} of a long agent-written summary that runs on and on.`,
    ).join(' ');
    const { root } = await mount([
      {
        findingId: 'fin_short',
        findingClass: 'style',
        locus: 'a.py:1',
        summary: 'one short line',
        state: 'live',
        lastSeenAt: '2026-01-05T00:00:00Z',
      },
      {
        findingId: 'fin_long',
        findingClass: 'style',
        locus: 'a.py:1',
        summary: LONG_SUMMARY,
        state: 'live',
        lastSeenAt: '2026-01-05T00:00:00Z',
      },
    ]);
    try {
      await page.viewport(1400, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      // A genuine one-line reference measured in the same rendered tree — not
      // `getComputedStyle().lineHeight`, which Chromium reports as the literal
      // keyword `normal` (unparseable) when no `line-height` is set.
      const oneLine = root.querySelector<HTMLElement>('[data-testid="gardening-finding-row-fin_short"] .fl-summary')!;
      const headline = root.querySelector<HTMLElement>('[data-testid="gardening-finding-row-fin_long"] .fl-summary')!;
      const oneLineHeight = oneLine.getBoundingClientRect().height;
      const clampedHeight = headline.getBoundingClientRect().height;

      // A genuinely clamped headline renders at (at most, allowing for rounding)
      // four lines tall regardless of how much text it carries — the twenty
      // sentences above run to well beyond four lines unclamped, so a
      // `-webkit-line-clamp: 4` declaration that jsdom would accept without
      // evaluating is not enough; this only passes if the browser actually clips
      // the box.
      expect(
        clampedHeight,
        `the clamped headline's rendered height (${clampedHeight}px) exceeds four lines (${oneLineHeight * 4}px) — the summary is not actually clamping`,
      ).toBeLessThanOrEqual(oneLineHeight * 4 + 1);
    } finally {
      root.remove();
    }
  });
});
