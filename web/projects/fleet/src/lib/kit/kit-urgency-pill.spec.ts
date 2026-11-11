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
    expect(getComputedStyle(pill).color).toBe('rgb(0, 0, 0)');
    expect(pill.style.getPropertyValue('--urgency-color')).toContain('--project-red');
    fixture.componentInstance.color.set('#000000');
    await fixture.whenStable();
    expect(pill.style.getPropertyValue('--urgency-color')).toBe(legibleOnDark('#000000'));
  });
  it.each(['#000000', '#1a1f4d', '#0000ff', '#3b0764', '#7f1d1d', '#064e3b'])('keeps black text above 4.5:1 on lifted %s', (color) => {
    const hex = legibleOnDark(color);
    const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((c) => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const luminance = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    expect((luminance + 0.05) / 0.05).toBeGreaterThanOrEqual(4.5);
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
    expect(pill.getAttribute('aria-label')).toBe('Urgent');
    expect(pill.textContent?.trim()).toBe('🔥');
    pill.dispatchEvent(new FocusEvent('focus'));
    await fixture.whenStable();
    expect(document.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe('Urgent — Needs attention');
    expect(pill.getAttribute('aria-describedby')).toBeTruthy();
    fixture.componentInstance.icon.set('/icon.png');
    await fixture.whenStable();
    expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('[aria-hidden="true"]')?.textContent).toBe('🔥');
    fixture.componentInstance.emoji.set(null);
    await fixture.whenStable();
    expect(root.querySelector('img')?.getAttribute('alt')).toBe('');
    expect(pill.textContent?.trim()).toBe('');
    expect(root.querySelector('[aria-hidden="true"]')).toBeNull();
  });
});
