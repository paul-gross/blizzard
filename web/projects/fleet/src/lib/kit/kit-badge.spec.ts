import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitBadge } from './kit-badge';
import type { Tone } from './tone';

@Component({
  selector: 'fleet-test-host',
  imports: [KitBadge],
  template: `<fleet-kit-badge [tone]="tone()" [variant]="variant()">RUNNING</fleet-kit-badge>`,
})
class TestHost {
  readonly tone = signal<Tone>('running');
  readonly variant = signal<'text' | 'pill' | 'soft'>('text');
}

describe('KitBadge', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('projects the label and colors it for the tone', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const badge = el.querySelector('.badge') as HTMLElement;
    expect(badge.textContent?.trim()).toBe('RUNNING');
    expect(badge.getAttribute('style')).toContain('var(--amber)');
  });

  it('maps every tone to its own color, matching the derived-status ladder', async () => {
    const fixture = TestBed.createComponent(TestHost);
    const expected: Record<Tone, string> = {
      running: 'var(--amber)',
      needs: 'var(--red)',
      waiting: 'var(--amber-hi)',
      takeover: 'var(--amber-hi)',
      spawning: 'var(--cyan)',
      stale: 'var(--red)',
      done: 'var(--green)',
      idle: 'var(--label-dim)',
    };
    for (const [tone, color] of Object.entries(expected) as [Tone, string][]) {
      fixture.componentInstance.tone.set(tone);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect((el.querySelector('.badge') as HTMLElement).getAttribute('style')).toContain(color);
    }
  });

  it('adds the pill class only for the pill variant', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    let el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.badge')?.classList.contains('pill')).toBe(false);

    fixture.componentInstance.variant.set('pill');
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.badge')?.classList.contains('pill')).toBe(true);
  });

  it('the soft variant keeps the tone color but adds a dimmed border and a tinted fill', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.variant.set('soft');
    fixture.componentInstance.tone.set('needs');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const badge = el.querySelector('.badge') as HTMLElement;
    expect(badge.classList.contains('soft')).toBe(true);
    expect(badge.classList.contains('pill')).toBe(false);
    const style = badge.getAttribute('style') ?? '';
    expect(style).toContain('var(--red)');
    expect(style).toContain('var(--red-dim)');
    expect(style).toContain('color-mix(in srgb, var(--red) 12%, transparent)');
  });
});
