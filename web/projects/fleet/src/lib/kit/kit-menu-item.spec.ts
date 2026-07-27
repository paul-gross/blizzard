import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { TestBed } from '@angular/core/testing';

import { KitMenu, KitMenuPanel } from './kit-menu';
import { KitMenuItem, KitMenuItemRadio } from './kit-menu-item';

@Component({
  selector: 'fleet-test-host',
  imports: [CdkMenuTrigger, KitMenu, KitMenuPanel, KitMenuItem, KitMenuItemRadio],
  template: `
    <fleet-kit-menu ariaLabel="Menu" testid="trigger" [menu]="panel" />
    <ng-template #panel>
      <fleet-kit-menu-panel testid="panel">
        <fleet-kit-menu-item testid="act" (triggered)="acted = acted + 1">Act</fleet-kit-menu-item>
        <fleet-kit-menu-item testid="off" [disabled]="true" (triggered)="acted = acted + 1">Off</fleet-kit-menu-item>
        <fleet-kit-menu-item testid="sub" submenu [cdkMenuTriggerFor]="subPanel">Appearance</fleet-kit-menu-item>
      </fleet-kit-menu-panel>
    </ng-template>
    <ng-template #subPanel>
      <fleet-kit-menu-panel testid="sub-panel">
        <fleet-kit-menu-item-radio testid="one" [checked]="choice() === 'one'" (triggered)="choice.set('one')">
          One
        </fleet-kit-menu-item-radio>
        <fleet-kit-menu-item-radio testid="two" [checked]="choice() === 'two'" (triggered)="choice.set('two')">
          Two
        </fleet-kit-menu-item-radio>
      </fleet-kit-menu-panel>
    </ng-template>
  `,
})
class TestHost {
  readonly choice = signal('one');
  acted = 0;
}

const inOverlay = (selector: string) => document.body.querySelector<HTMLElement>(selector);

describe('KitMenuItem', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<TestHost>>;

  const open = async () => {
    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="trigger"]')?.click();
    await fixture.whenStable();
  };

  /** The CDK reads the legacy `keyCode`, so a spec's synthetic event must carry
   * it — a `key`-only event is silently ignored by every CDK key handler. */
  const keydown = (selector: string, key: string, keyCode: number) => {
    inOverlay(selector)?.dispatchEvent(new KeyboardEvent('keydown', { key, keyCode, bubbles: true }));
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    await open();
  });

  it('registers each item with its panel as a real menu (issue #161)', () => {
    const panel = inOverlay('[data-testid="panel"]');
    expect(panel?.getAttribute('role')).toBe('menu');
    expect(panel?.querySelectorAll('[role="menuitem"]').length).toBe(3);
    // Roving tabindex: exactly one item is in the tab order, the rest are
    // reachable by arrow key — the focus management the home-grown popover
    // never had.
    expect(Array.from(panel!.querySelectorAll('[role="menuitem"]')).map((i) => i.getAttribute('tabindex'))).toEqual([
      '0',
      '-1',
      '-1',
    ]);
  });

  it('fires triggered on click and on Enter, but never while disabled', async () => {
    inOverlay('[data-testid="act"]')?.click();
    await fixture.whenStable();
    expect(fixture.componentInstance.acted).toBe(1);

    await open();
    keydown('[data-testid="act"]', 'Enter', 13);
    await fixture.whenStable();
    expect(fixture.componentInstance.acted).toBe(2);

    await open();
    expect(inOverlay('[data-testid="off"]')?.getAttribute('aria-disabled')).toBe('true');
    inOverlay('[data-testid="off"]')?.click();
    await fixture.whenStable();
    expect(fixture.componentInstance.acted).toBe(2);
  });

  it('opens a submenu with the right arrow and returns to the parent with the left', async () => {
    expect(inOverlay('[data-testid="sub"]')?.getAttribute('aria-haspopup')).toBe('menu');
    expect(inOverlay('[data-testid="sub-panel"]')).toBeNull();

    inOverlay('[data-testid="sub"]')?.focus();
    keydown('[data-testid="sub"]', 'ArrowRight', 39);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="sub-panel"]')).not.toBeNull();
    expect(inOverlay('[data-testid="sub"]')?.getAttribute('aria-expanded')).toBe('true');

    keydown('[data-testid="one"]', 'ArrowLeft', 37);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="sub-panel"]')).toBeNull();
    expect(inOverlay('[data-testid="panel"]')).not.toBeNull();
  });

  it('pins the CDK hover-then-click behavior: the pointer opens the submenu, the click shuts it', async () => {
    // The real mouse gesture, which `element.click()` alone never reproduces —
    // and which therefore used to be invisible to this tier. Hovering the row
    // fires the CDK's hover-open; the click that follows toggles it back shut.
    // Documented and pinned, not endorsed: see the class docs on KitMenuItem.
    // Keyboard and touch, the paths the shells actually rely on, are covered by
    // the arrow-key and closed-submenu specs either side of this one.
    inOverlay('[data-testid="sub"]')?.dispatchEvent(new MouseEvent('mouseenter', { bubbles: false }));
    await fixture.whenStable();
    expect(inOverlay('[data-testid="sub-panel"]')).not.toBeNull();

    inOverlay('[data-testid="sub"]')?.click();
    await fixture.whenStable();

    expect(inOverlay('[data-testid="sub-panel"]')).toBeNull();
  });

  it('still opens a closed submenu on click — the only gesture touch has', async () => {
    expect(inOverlay('[data-testid="sub-panel"]')).toBeNull();

    inOverlay('[data-testid="sub"]')?.click();
    await fixture.whenStable();

    expect(inOverlay('[data-testid="sub-panel"]')).not.toBeNull();
  });

  it('owns only menu items, per the role="menu" content model', () => {
    const panel = inOverlay('[data-testid="panel"]');
    const untyped = Array.from(panel!.children).filter(
      (child) => !['menuitem', 'menuitemradio', 'menuitemcheckbox', 'group', 'separator', 'presentation'].includes(
        child.getAttribute('role') ?? '',
      ),
    );
    expect(untyped).toEqual([]);
  });

  it('marks its radio group so the checked option and assistive tech agree', async () => {
    inOverlay('[data-testid="sub"]')?.click();
    await fixture.whenStable();

    expect(inOverlay('[data-testid="one"]')?.getAttribute('role')).toBe('menuitemradio');
    expect(inOverlay('[data-testid="one"]')?.getAttribute('aria-checked')).toBe('true');
    expect(inOverlay('[data-testid="two"]')?.getAttribute('aria-checked')).toBe('false');

    inOverlay('[data-testid="two"]')?.click();
    await fixture.whenStable();

    expect(fixture.componentInstance.choice()).toBe('two');
  });
});
