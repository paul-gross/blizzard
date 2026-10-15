import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import { KitTabs, type KitTabOption } from './kit-tabs';

const OPTIONS: KitTabOption[] = [
  { value: 'overview', label: 'Overview' },
  { value: 'sources', label: 'Linked sources', count: 2 },
];

@Component({
  imports: [KitTabs],
  template: `
    <fleet-kit-tabs data-testid="top" [options]="options" activeValue="overview" />
    <fleet-kit-tabs data-testid="sub" variant="sub" [options]="options" activeValue="overview" />
  `,
})
class TwoTierHost {
  options = OPTIONS;
}

/** The design tokens are a global stylesheet the app build loads, never a standalone mount —
 * injected so the strips' colours resolve to real values. */
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

/** The two strip tiers as a reader sees them — real computed colours and layout heights. */
function measure(root: HTMLElement, testid: string): { height: string; color: string; background: string; shadow: string } {
  const strip = root.querySelector<HTMLElement>(`[data-testid="${testid}"] [role="tablist"]`)!;
  const active = strip.querySelector<HTMLElement>('.tab.active')!;
  const style = getComputedStyle(active);
  return {
    height: getComputedStyle(strip).height,
    color: style.color,
    background: getComputedStyle(strip).backgroundColor,
    shadow: style.boxShadow,
  };
}

describe('KitTabs sub-strip variant shell sweep', () => {
  for (const width of [1280, 390]) {
    it(`renders the sub strip shorter and cyan-active beside the amber top strip at ${width}px`, async () => {
      await page.viewport(width, 800);
      await loadDesignTokens();
      await TestBed.configureTestingModule({
        imports: [TwoTierHost],
        providers: [provideZonelessChangeDetection()],
      }).compileComponents();
      const fixture = TestBed.createComponent(TwoTierHost);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        const top = measure(root, 'top');
        const sub = measure(root, 'sub');

        expect(top.height).toBe('32px');
        expect(sub.height).toBe('30px');
        expect(top.color).toBe(tokenColor('--amber-hi'));
        expect(sub.color).toBe(tokenColor('--cyan'));
        expect(sub.color).not.toBe(top.color);
        expect(sub.background).toBe(tokenColor('--panel-deep'));
        expect(sub.shadow).toContain(tokenColor('--cyan-dim'));
        expect(top.shadow).toBe('none');
      } finally {
        fixture.destroy();
        root.remove();
        await page.viewport(1280, 800);
      }
    });
  }
});
