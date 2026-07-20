import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitMenu } from './kit-menu';

@Component({
  selector: 'fleet-test-host',
  imports: [KitMenu],
  template: `
    <fleet-kit-menu ariaLabel="Shell options" testid="the-menu">
      <p data-testid="menu-body">projected content</p>
    </fleet-kit-menu>
    <button type="button" data-testid="outside">outside</button>
  `,
})
class TestHost {}

describe('KitMenu', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('hides the projected content until the trigger is clicked', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="menu-body"]')).toBeNull();

    el.querySelector<HTMLElement>('[data-testid="the-menu"]')?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="menu-body"]')?.textContent).toBe('projected content');
    expect(el.querySelector('[data-testid="the-menu-panel"] [data-testid="menu-body"]')).not.toBeNull();
  });

  it('closes again on a second trigger click', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const trigger = () => el.querySelector<HTMLElement>('[data-testid="the-menu"]')!;

    trigger().click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="menu-body"]')).not.toBeNull();

    trigger().click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="menu-body"]')).toBeNull();
  });

  it('closes on an outside click, but not a click inside the panel', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="the-menu"]')?.click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="menu-body"]')).not.toBeNull();

    el.querySelector<HTMLElement>('[data-testid="menu-body"]')?.click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="menu-body"]')).not.toBeNull();

    el.querySelector<HTMLElement>('[data-testid="outside"]')?.click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="menu-body"]')).toBeNull();
  });

  it('closes on Escape', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="the-menu"]')?.click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="menu-body"]')).not.toBeNull();

    // Dispatched on the panel itself so it bubbles up through the host's
    // `(keydown.escape)` listener, not on the fixture root (which would never
    // reach it — events bubble up from the target, not down into it).
    el.querySelector('[data-testid="the-menu-panel"]')?.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    );
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="menu-body"]')).toBeNull();
  });
});
