import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitSkeleton } from './kit-skeleton';

describe('KitSkeleton', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [KitSkeleton],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders three line bars by default', async () => {
    const fixture = TestBed.createComponent(KitSkeleton);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const bars = el.querySelectorAll('.bar');
    expect(bars).toHaveLength(3);
    expect([...bars].every((bar) => !bar.classList.contains('card'))).toBe(true);
  });

  it('renders the given row count', async () => {
    const fixture = TestBed.createComponent(KitSkeleton);
    fixture.componentRef.setInput('rows', 5);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('.bar')).toHaveLength(5);
  });

  it('renders the card variant when asked', async () => {
    const fixture = TestBed.createComponent(KitSkeleton);
    fixture.componentRef.setInput('variant', 'card');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect([...el.querySelectorAll('.bar')].every((bar) => bar.classList.contains('card'))).toBe(true);
  });
});
