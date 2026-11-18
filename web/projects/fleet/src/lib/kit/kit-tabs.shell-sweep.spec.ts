import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page, userEvent } from 'vitest/browser';

import { KitTabContent } from './kit-tab-content';
import { KitTabGroup } from './kit-tab-group';
import { KitTabPanel } from './kit-tab-panel';
import { KitTabs, type KitTabOption } from './kit-tabs';

const OPTIONS: KitTabOption[] = [
  { value: 'overview', label: 'Overview' },
  { value: 'sources', label: 'Linked sources', count: 2 },
];

@Component({
  imports: [KitTabs, KitTabGroup],
  template: `
    <div fleetKitTabGroup>
      <fleet-kit-tabs data-testid="top" [options]="options" active="overview" />
    </div>
    <div fleetKitTabGroup>
      <fleet-kit-tabs data-testid="sub" variant="sub" [options]="options" active="overview" />
    </div>
  `,
})
class TwoTierHost {
  options = OPTIONS;
}

const THREE: KitTabOption[] = [
  { value: 'overview', label: 'Overview', testid: 'k-overview' },
  { value: 'sources', label: 'Linked sources', testid: 'k-sources' },
  { value: 'repos', label: 'Repositories', testid: 'k-repos' },
];

@Component({
  imports: [KitTabs, KitTabGroup, KitTabPanel, KitTabContent],
  template: `
    <button data-testid="before" type="button">before</button>
    <section fleetKitTabGroup>
      <fleet-kit-tabs [options]="options" [(active)]="active" (activeChange)="changes.push($event)" />
      @for (option of options; track option.value) {
        <div [fleetKitTabPanel]="option.value" [attr.data-testid]="'panel-' + option.value">
          <ng-template fleetKitTabContent><p [attr.data-testid]="'body-' + option.value">{{ option.label }} body</p></ng-template>
        </div>
      }
    </section>
    <button data-testid="after" type="button">after</button>
  `,
})
class KeyboardHost {
  options = THREE;
  readonly active = signal<string | null>('overview');
  readonly changes: (string | null)[] = [];
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

describe('KitTabs keyboard model shell sweep', () => {
  async function mount(): Promise<{ fixture: ReturnType<typeof TestBed.createComponent<KeyboardHost>>; root: HTMLElement }> {
    await page.viewport(1280, 800);
    await loadDesignTokens();
    await TestBed.configureTestingModule({
      imports: [KeyboardHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(KeyboardHost);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    return { fixture, root };
  }
  const q = (root: HTMLElement, testid: string) => root.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!;
  const settle = (fixture: { whenStable(): Promise<unknown> }) => new Promise<void>((done) => setTimeout(() => fixture.whenStable().then(() => done()), 50));

  it('is one Tab stop, moves with Left/Right/Home/End, and shows only the matching panel', async () => {
    const { fixture, root } = await mount();
    try {
      // Tab from a prior focusable lands on the selected tab; the next Tab leaves the strip.
      q(root, 'before').focus();
      await userEvent.keyboard('{Tab}');
      expect(document.activeElement).toBe(q(root, 'k-overview'));
      await userEvent.keyboard('{Tab}');
      expect(document.activeElement).toBe(q(root, 'after'));

      q(root, 'k-overview').focus();
      for (const [key, active] of [
        ['ArrowRight', 'sources'],
        ['ArrowRight', 'repos'],
        ['Home', 'overview'],
        ['End', 'repos'],
        ['ArrowLeft', 'sources'],
      ] as const) {
        await userEvent.keyboard(`{${key}}`);
        await settle(fixture);
        expect(fixture.componentInstance.active()).toBe(active);
        expect(document.activeElement).toBe(q(root, `k-${active}`));
        for (const option of THREE) {
          const panel = q(root, `panel-${option.value}`);
          const visible = option.value === active;
          expect(getComputedStyle(panel).display === 'none').toBe(!visible);
          expect(root.querySelector(`[data-testid="body-${option.value}"]`) !== null).toBe(visible);
        }
      }
      expect(fixture.componentInstance.changes).toEqual(['sources', 'repos', 'overview', 'repos', 'sources']);
    } finally {
      fixture.destroy();
      root.remove();
    }
  });
});
