import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { KitUrgencyPill } from './kit-urgency-pill';
import { legibleOnDark } from './kit-swatch-color';

@Component({
  selector: 'fleet-test-host',
  imports: [KitUrgencyPill],
  template: `<fleet-kit-urgency-pill name="Urgent" [color]="color()" [emoji]="emoji()" [iconUrl]="icon()" [description]="description()" />`,
})
class TestHost {
  readonly color = signal('red');
  readonly emoji = signal<string | null>(null);
  readonly icon = signal<string | null>(null);
  readonly description = signal<string | null>(null);
}

describe('KitUrgencyPill', () => {
  beforeEach(() => TestBed.configureTestingModule({ imports: [TestHost], providers: [provideZonelessChangeDetection()] }));
  it('renders name, palette and lifted custom colors', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const pill = fixture.nativeElement.querySelector('.pill') as HTMLElement;
    expect(pill.textContent?.trim()).toBe('Urgent');
    expect(pill.style.getPropertyValue('--urgency-color')).toContain('--project-red');
    fixture.componentInstance.color.set('#000000');
    await fixture.whenStable();
    expect(pill.style.getPropertyValue('--urgency-color')).toBe(legibleOnDark('#000000'));
  });
  it('renders decorative emoji or image and a keyboard-accessible description', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.emoji.set('🔥');
    fixture.componentInstance.description.set('Needs attention');
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('[aria-hidden="true"]')?.textContent).toBe('🔥');
    const pill = root.querySelector('.pill') as HTMLElement;
    expect(pill.tabIndex).toBe(0);
    pill.dispatchEvent(new FocusEvent('focus'));
    await fixture.whenStable();
    expect(document.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe('Needs attention');
    expect(pill.getAttribute('aria-describedby')).toBeTruthy();
    fixture.componentInstance.icon.set('/icon.png');
    await fixture.whenStable();
    expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('[aria-hidden="true"]')?.textContent).toBe('🔥');
    fixture.componentInstance.emoji.set(null);
    await fixture.whenStable();
    expect(root.querySelector('img')?.getAttribute('alt')).toBe('');
    expect(root.querySelector('[aria-hidden="true"]')).toBeNull();
  });
});
