import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page, userEvent } from 'vitest/browser';

import { KitSelectRow } from './kit-select-row';
import { KitUrgencyPill } from './kit-urgency-pill';

/**
 * `KitSelectRow`'s default chrome and its `bare` opt-out, rendered — computed padding,
 * borders and backgrounds, and whether a flex rule on the host actually reaches the
 * projected children through the inner button, are layout and cascade claims jsdom
 * cannot make.
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`) because
 * it needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */

@Component({
  imports: [KitSelectRow],
  template: `
    <fleet-kit-select-row data-testid="plain" selected testid="plain-btn"><span>Plain</span></fleet-kit-select-row>
    <fleet-kit-select-row class="flex-host" data-testid="bare" bare selected testid="bare-btn">
      <span class="a">A</span><span class="b">B</span>
    </fleet-kit-select-row>
  `,
  styles: `
    .flex-host {
      display: flex;
      flex-direction: row;
      align-items: center;
      gap: 12px;
    }
  `,
})
class Host {}

const longName = 'UnbrokenUrgencyName'.repeat(8);

@Component({
  imports: [KitUrgencyPill],
  template: `<fleet-kit-urgency-pill [name]="name" color="cyan" description="Details" />`,
  styles: `:host { display: block; width: 100%; }`,
})
class UrgencyHost {
  readonly name = longName;
}

describe('KitUrgencyPill narrow layout', () => {
  it('clamps an unbroken name while retaining its accessible name and tooltip', async () => {
    await page.viewport(320, 800);
    await TestBed.configureTestingModule({
      imports: [UrgencyHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(UrgencyHost);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    try {
      const pill = root.querySelector<HTMLElement>('.pill')!;
      const name = root.querySelector<HTMLElement>('.name')!;
      expect(pill.getBoundingClientRect().right).toBeLessThanOrEqual(document.documentElement.clientWidth);
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(document.documentElement.clientWidth);
      expect(name.scrollWidth).toBeGreaterThan(name.clientWidth);
      expect(getComputedStyle(name).textOverflow).toBe('ellipsis');
      expect(pill.getAttribute('aria-label')).toBe(longName);
      await userEvent.hover(pill);
      await expect.poll(() => document.body.textContent).toContain(`${longName} — Details`);
    } finally {
      root.remove();
    }
  });
});

/** The design tokens are a global stylesheet the app build loads, never a standalone mount. */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

function tokenBackground(token: string): string {
  const probe = document.createElement('span');
  probe.style.backgroundColor = `var(${token})`;
  document.body.appendChild(probe);
  const color = getComputedStyle(probe).backgroundColor;
  probe.remove();
  return color;
}

describe('KitSelectRow shell sweep', () => {
  for (const width of [800, 390, 320]) {
    it(`keeps the default chrome and strips it under bare at ${width}px`, async () => {
      await page.viewport(width, 800);
      await loadDesignTokens();
      await TestBed.configureTestingModule({
        imports: [Host],
        providers: [provideZonelessChangeDetection()],
      }).compileComponents();
      const fixture = TestBed.createComponent(Host);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        const plain = root.querySelector<HTMLElement>('[data-testid="plain-btn"]')!;
        const plainStyle = getComputedStyle(plain);
        expect(plainStyle.paddingTop).toBe('6px');
        expect(plainStyle.paddingLeft).toBe('8px');
        expect(plainStyle.borderBottomWidth).toBe('1px');
        expect(plainStyle.borderLeftWidth).toBe('2px');
        expect(plainStyle.backgroundColor).toBe(tokenBackground('--surface-selected'));

        const bare = root.querySelector<HTMLElement>('[data-testid="bare-btn"]')!;
        for (const hovered of [false, true]) {
          if (hovered) await userEvent.hover(bare);
          const style = getComputedStyle(bare);
          expect(style.paddingTop).toBe('0px');
          expect(style.paddingLeft).toBe('0px');
          expect(style.borderBottomWidth).toBe('0px');
          expect(style.borderLeftWidth).toBe('0px');
          expect(style.backgroundColor).toBe('rgba(0, 0, 0, 0)');
        }

        const a = root.querySelector<HTMLElement>('.a')!.getBoundingClientRect();
        const b = root.querySelector<HTMLElement>('.b')!.getBoundingClientRect();
        expect(Math.abs(a.top + a.height / 2 - (b.top + b.height / 2))).toBeLessThanOrEqual(1);
        expect(b.left - a.right).toBeCloseTo(12, 0);
      } finally {
        root.remove();
      }
    });
  }
});
