import { Component, provideZonelessChangeDetection } from '@angular/core';
import { CdkMenuTrigger } from '@angular/cdk/menu';
import { TestBed } from '@angular/core/testing';

import { KitMenu, KitMenuPanel } from './kit-menu';
import { KitMenuItem, KitMenuItemRadio } from './kit-menu-item';

@Component({
  selector: 'fleet-test-host',
  imports: [CdkMenuTrigger, KitMenu, KitMenuPanel, KitMenuItem, KitMenuItemRadio],
  template: `
    <fleet-kit-menu ariaLabel="Shell options" testid="the-menu" [menu]="panel" />
    <button type="button" data-testid="outside">outside</button>
    <ng-template #panel>
      <fleet-kit-menu-panel testid="the-menu-panel">
        <fleet-kit-menu-item testid="menu-body" (triggered)="triggered = true">projected content</fleet-kit-menu-item>
        <fleet-kit-menu-item testid="menu-sub" submenu [cdkMenuTriggerFor]="sub">Appearance</fleet-kit-menu-item>
      </fleet-kit-menu-panel>
    </ng-template>
    <ng-template #sub>
      <fleet-kit-menu-panel testid="the-submenu-panel">
        <fleet-kit-menu-item-radio testid="sub-one" [checked]="true">One</fleet-kit-menu-item-radio>
        <fleet-kit-menu-item-radio testid="sub-two" (triggered)="chose = 'two'">Two</fleet-kit-menu-item-radio>
      </fleet-kit-menu-panel>
    </ng-template>
  `,
})
class TestHost {
  triggered = false;
  chose: string | null = null;
}

@Component({
  selector: 'fleet-test-host-custom-trigger',
  imports: [KitMenu, KitMenuPanel, KitMenuItem],
  template: `
    <fleet-kit-menu ariaLabel="Profile menu" testid="the-menu" [menu]="panel">
      <span trigger data-testid="custom-trigger">avatar</span>
    </fleet-kit-menu>
    <ng-template #panel>
      <fleet-kit-menu-panel><fleet-kit-menu-item>projected content</fleet-kit-menu-item></fleet-kit-menu-panel>
    </ng-template>
  `,
})
class TestHostCustomTrigger {}

/** The CDK renders every menu into an overlay attached to `document.body`, not
 * inside the fixture's own element — so panel assertions query the document. */
const inOverlay = (selector: string) => document.body.querySelector<HTMLElement>(selector);

describe('KitMenu', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<TestHost>>;
  let el: HTMLElement;

  const open = async () => {
    el.querySelector<HTMLElement>('[data-testid="the-menu"]')?.click();
    await fixture.whenStable();
  };

  /** The CDK reads the legacy `keyCode`, so a spec's synthetic event must carry
   * it — a `key`-only event is silently ignored by every CDK key handler. */
  const keydown = (target: Element | null, key: string, keyCode: number) =>
    target?.dispatchEvent(new KeyboardEvent('keydown', { key, keyCode, bubbles: true }));

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost, TestHostCustomTrigger],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
  });

  it('hides the panel until the trigger is clicked', async () => {
    expect(inOverlay('[data-testid="menu-body"]')).toBeNull();

    await open();

    expect(inOverlay('[data-testid="the-menu-panel"] [data-testid="menu-body"]')?.textContent?.trim()).toBe(
      'projected content',
    );
  });

  it('closes again on a second trigger click', async () => {
    await open();
    expect(inOverlay('[data-testid="menu-body"]')).not.toBeNull();

    await open();
    expect(inOverlay('[data-testid="menu-body"]')).toBeNull();
  });

  it('closes on an outside click, but not a click inside the panel', async () => {
    await open();

    inOverlay('[data-testid="menu-sub"]')?.click();
    await fixture.whenStable();
    expect(inOverlay('[data-testid="menu-body"]')).not.toBeNull();

    el.querySelector<HTMLElement>('[data-testid="outside"]')?.dispatchEvent(
      new PointerEvent('pointerdown', { bubbles: true }),
    );
    el.querySelector<HTMLElement>('[data-testid="outside"]')?.click();
    await fixture.whenStable();
    expect(inOverlay('[data-testid="menu-body"]')).toBeNull();
  });

  it('closes on Escape', async () => {
    await open();
    expect(inOverlay('[data-testid="menu-body"]')).not.toBeNull();

    keydown(inOverlay('[data-testid="the-menu-panel"]'), 'Escape', 27);
    await fixture.whenStable();

    expect(inOverlay('[data-testid="menu-body"]')).toBeNull();
  });

  it('defaults the trigger to the ⋮ glyph when no [trigger] content is projected', () => {
    expect(el.querySelector<HTMLElement>('[data-testid="the-menu"]')?.textContent?.trim()).toBe('⋮');
  });

  it('renders projected [trigger] content instead of the default glyph (issue #132)', async () => {
    const custom = TestBed.createComponent(TestHostCustomTrigger);
    await custom.whenStable();
    const trigger = (custom.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="the-menu"]');

    expect(trigger?.querySelector('[data-testid="custom-trigger"]')).not.toBeNull();
    expect(trigger?.textContent?.trim()).not.toBe('⋮');
  });

  it('conveys its expanded state on the trigger (issue #161)', async () => {
    const trigger = () => el.querySelector<HTMLElement>('[data-testid="the-menu"]');
    expect(trigger()?.getAttribute('aria-haspopup')).toBe('menu');
    expect(trigger()?.getAttribute('aria-expanded')).toBe('false');

    await open();

    expect(trigger()?.getAttribute('aria-expanded')).toBe('true');
  });
});
