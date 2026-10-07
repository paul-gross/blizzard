import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page } from 'vitest/browser';

import { MobileTabBar, type MobileTabItem } from './mobile-tab-bar';

/** The design tokens are a global stylesheet the app build loads, never a standalone mount —
 * injected so the count chip's fill resolves to a real colour. */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

/** The computed colour a design token resolves to, read off a probe element. */
function tokenColor(token: string): string {
  const probe = document.createElement('span');
  probe.style.color = `var(${token})`;
  document.body.appendChild(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}

/**
 * The shared mobile bottom tab bar's count badge — a real, headless-Chromium proof that
 * an open-ask count renders as the kit's amber count chip and stays inside its own tab
 * and the bar at phone widths (`bzh:narrow-viewport-tier-rule`), across the four-tab
 * runner strip, the widest the bar carries, with a two-digit count.
 *
 * Excluded from the default `ng test` run (`angular.json`'s `test.exclude`) because it
 * needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const ITEMS: readonly MobileTabItem[] = [
  { testid: 'tab-board', label: 'Board', active: true },
  { testid: 'tab-asks', label: 'Asks', badge: 12, badgeTestid: 'tab-asks-badge' },
  { testid: 'tab-transcripts', label: 'Transcripts' },
  { testid: 'tab-events', label: 'Events' },
];

describe('mobile tab bar count badge shell sweep (web:shell-sweep)', () => {
  for (const width of [390, 320]) {
    it(`renders the Asks count as the amber kit count chip inside its tab and the bar at ${width}px`, async () => {
      await loadDesignTokens();
      TestBed.resetTestingModule();
      await TestBed.configureTestingModule({
        imports: [MobileTabBar],
        providers: [provideZonelessChangeDetection(), provideRouter([])],
      }).compileComponents();
      const fixture = TestBed.createComponent(MobileTabBar);
      fixture.componentRef.setInput('items', ITEMS);
      await fixture.whenStable();
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);

      try {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const badge = root.querySelector<HTMLElement>('[data-testid="tab-asks-badge"]')!;
        expect(badge.closest('fleet-kit-count-badge')).not.toBeNull();
        expect(badge.textContent?.trim()).toBe('12');
        expect(getComputedStyle(badge).backgroundColor).toBe(tokenColor('--amber'));

        const tab = root.querySelector<HTMLElement>('[data-testid="tab-asks"]')!;
        const bar = root.querySelector<HTMLElement>('[data-testid="mobile-tab-bar"]')!;
        const b = badge.getBoundingClientRect();
        const t = tab.getBoundingClientRect();
        expect(b.left, `${width}px: badge starts before its tab`).toBeGreaterThanOrEqual(t.left);
        expect(b.right, `${width}px: badge spills past its tab`).toBeLessThanOrEqual(t.right);
        expect(b.top, `${width}px: badge rises above the bar`).toBeGreaterThanOrEqual(bar.getBoundingClientRect().top);
        expect(b.bottom, `${width}px: badge drops below the bar`).toBeLessThanOrEqual(bar.getBoundingClientRect().bottom);
        expect(bar.scrollWidth, `${width}px: tab bar overflows horizontally`).toBeLessThanOrEqual(bar.clientWidth);
      } finally {
        root.remove();
      }
    });
  }
});
