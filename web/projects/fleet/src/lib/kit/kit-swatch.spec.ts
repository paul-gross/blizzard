import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitSwatch } from './kit-swatch';

@Component({
  selector: 'fleet-test-host',
  imports: [KitSwatch],
  template: `<fleet-kit-swatch [color]="color()" [label]="label()" />`,
})
class TestHost {
  readonly color = signal('teal');
  readonly label = signal<string | null>(null);
}

function render() {
  TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
  const fixture = TestBed.createComponent(TestHost);
  fixture.detectChanges();
  return fixture;
}

describe('KitSwatch', () => {
  it('is decorative without a label', () => {
    const el: HTMLElement = render().nativeElement.querySelector('.swatch');
    expect(el.getAttribute('aria-hidden')).toBe('true');
    expect(el.getAttribute('role')).toBeNull();
    expect(el.style.background).toContain('--project-teal');
  });

  it('draws a custom hex color as itself, lowercased, once it is light enough', () => {
    const fixture = render();
    fixture.componentInstance.color.set('#FF8800');
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement.querySelector('.swatch');
    expect(el.style.background).toBe('rgb(255, 136, 0)');
  });

  it('lifts a dark custom color rather than drawing it into the surface', () => {
    const fixture = render();
    fixture.componentInstance.color.set('#1a1f4d');
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement.querySelector('.swatch');
    expect(el.style.background).not.toBe('rgb(26, 31, 77)');
    expect(el.style.background).toMatch(/^rgb\(/);
  });

  it('is an image named by its label, with the label as its tooltip', () => {
    const fixture = render();
    fixture.componentInstance.label.set('Widgets');
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement.querySelector('.swatch');
    expect(el.getAttribute('role')).toBe('img');
    expect(el.getAttribute('aria-label')).toBe('Widgets');
    expect(el.getAttribute('title')).toBe('Widgets');
    expect(el.getAttribute('aria-hidden')).toBeNull();
  });
});
