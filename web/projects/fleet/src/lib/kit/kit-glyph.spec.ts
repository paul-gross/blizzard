import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitGlyph, type KitGlyphName } from './kit-glyph';

@Component({
  selector: 'fleet-test-host',
  imports: [KitGlyph],
  template: `<fleet-kit-glyph [glyph]="glyph()" [label]="label()" />`,
})
class TestHost {
  readonly glyph = signal<KitGlyphName>('pause');
  readonly label = signal('Paused by runner');
}

async function render(glyph: KitGlyphName): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(TestHost);
  fixture.componentInstance.glyph.set(glyph);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('KitGlyph', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('draws pause as bars in a square', async () => {
    const el = await render('pause');

    expect(el.querySelector('fleet-kit-glyph')?.getAttribute('data-glyph')).toBe('pause');
    expect(el.querySelector('svg rect')).not.toBeNull();
  });

  it('draws stop as a barred octagon, not a square', async () => {
    const el = await render('stop');

    expect(el.querySelector('fleet-kit-glyph')?.getAttribute('data-glyph')).toBe('stop');
    expect(el.querySelector('svg rect')).toBeNull();
    expect(el.querySelectorAll('svg path')).toHaveLength(2);
  });

  it('carries its label as both tooltip and accessible name', async () => {
    const el = await render('pause');

    expect(el.querySelector('fleet-kit-glyph')?.getAttribute('title')).toBe('Paused by runner');
    expect(el.querySelector('svg')?.getAttribute('role')).toBe('img');
    expect(el.querySelector('svg')?.getAttribute('aria-label')).toBe('Paused by runner');
  });
});
