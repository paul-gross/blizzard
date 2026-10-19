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
