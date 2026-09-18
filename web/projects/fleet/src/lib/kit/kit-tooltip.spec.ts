import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitTooltip } from './kit-tooltip';

@Component({
  imports: [KitTooltip],
  template: `<button type="button" [fleetTooltip]="text" data-testid="trigger">Ship</button>`,
})
class Host {
  text = 'Ships the selected chunk';
}

describe('KitTooltip', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<Host>>;
  let trigger: HTMLElement;

  const panel = () => document.querySelector<HTMLElement>('[role="tooltip"]');

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Host],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    trigger = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="trigger"]')!;
  });

  it('renders no panel until the trigger is hovered or focused', () => {
    expect(panel()).toBeNull();
    expect(trigger.getAttribute('aria-describedby')).toBeNull();
  });

  it('opens on mouseenter and closes on mouseleave', async () => {
    trigger.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
    await fixture.whenStable();
    expect(panel()?.textContent?.trim()).toBe('Ships the selected chunk');

    trigger.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));
    await fixture.whenStable();
    expect(panel()).toBeNull();
  });

  it('opens on keyboard focus and closes on blur', async () => {
    trigger.dispatchEvent(new FocusEvent('focus', { bubbles: true }));
    await fixture.whenStable();
    expect(panel()).not.toBeNull();

    trigger.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
    await fixture.whenStable();
    expect(panel()).toBeNull();
  });

  it('closes on Escape', async () => {
    trigger.dispatchEvent(new FocusEvent('focus', { bubbles: true }));
    await fixture.whenStable();
    expect(panel()).not.toBeNull();

    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await fixture.whenStable();
    expect(panel()).toBeNull();
  });

  it('points aria-describedby at the open panel’s own id, and clears it once closed', async () => {
    trigger.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
    await fixture.whenStable();

    const describedBy = trigger.getAttribute('aria-describedby');
    expect(describedBy).not.toBeNull();
    expect(panel()?.id).toBe(describedBy);

    trigger.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));
    await fixture.whenStable();
    expect(trigger.getAttribute('aria-describedby')).toBeNull();
  });

  it('tears the panel down when the host is destroyed while open', async () => {
    trigger.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
    await fixture.whenStable();
    expect(panel()).not.toBeNull();

    fixture.destroy();
    expect(panel()).toBeNull();
  });
});
