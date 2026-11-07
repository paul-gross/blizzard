import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitSectionHead } from './kit-section-head';

@Component({
  selector: 'fleet-test-host',
  imports: [KitSectionHead],
  template: `
    <fleet-kit-section-head [label]="label()" [labelId]="labelId()" [count]="count()">
      <a class="trail" href="#">graph</a>
    </fleet-kit-section-head>
  `,
})
class TestHost {
  readonly label = signal('Messages');
  readonly labelId = signal<string | null>(null);
  readonly count = signal<number | null>(null);
}

describe('KitSectionHead', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the label in the tag, with no id by default', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const tag = (fixture.nativeElement as HTMLElement).querySelector('.tag');

    expect(tag?.textContent?.trim()).toBe('Messages');
    expect(tag?.hasAttribute('id')).toBe(false);
  });

  it('carries labelId through to the tag, so aria-labelledby resolves', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.labelId.set('section-heading');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('#section-heading')?.textContent?.trim()).toBe('Messages');
  });

  it('suffixes the count as label · count, including zero', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.count.set(0);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.tag')?.textContent?.trim()).toBe('Messages · 0');

    fixture.componentInstance.count.set(3);
    await fixture.whenStable();
    expect(el.querySelector('.tag')?.textContent?.trim()).toBe('Messages · 3');
  });

  it('projects trailing content beside the tag, outside it', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.s-head > a.trail')?.textContent).toBe('graph');
    expect(el.querySelector('.tag a.trail')).toBeNull();
  });
});
