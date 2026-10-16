import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { FleetWhen } from './fleet-when';

@Component({
  selector: 'fleet-test-host',
  imports: [FleetWhen],
  template: `<fleet-when class="time" data-testid="stamp" [iso]="iso()" [relative]="relative()" />`,
})
class TestHost {
  readonly iso = signal('');
  readonly relative = signal(false);
}

describe('FleetWhen', () => {
  beforeEach(async () => {
    vi.setSystemTime(new Date(2026, 6, 18, 15, 30));
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders formatWhen's short text with formatAbsolute's text as the title", async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.iso.set(new Date(2026, 6, 18, 9, 5, 3).toISOString());
    await fixture.whenStable();
    const el = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="stamp"]') as HTMLElement;

    expect(el.textContent?.trim()).toBe('09:05');
    expect(el.getAttribute('title')).toBe('2026/07/18 09:05:03');
  });

  it("keeps the host's class and data-testid attributes, same as the span it replaces", async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.iso.set(new Date(2026, 6, 18, 9, 5, 3).toISOString());
    await fixture.whenStable();
    const el = (fixture.nativeElement as HTMLElement).querySelector('fleet-when') as HTMLElement;

    expect(el.classList.contains('time')).toBe(true);
    expect(el.getAttribute('data-testid')).toBe('stamp');
  });

  it('renders the relative distance when opted in, keeping the absolute tooltip', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.iso.set(new Date(2026, 6, 15, 15, 30).toISOString());
    fixture.componentInstance.relative.set(true);
    await fixture.whenStable();
    const el = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="stamp"]') as HTMLElement;

    expect(el.textContent?.trim()).toBe('3 days ago');
    expect(el.getAttribute('title')).toBe('2026/07/15 15:30:00');
  });

  it('renders no title for an unparseable input, never "Invalid Date"', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.iso.set('not-a-date');
    await fixture.whenStable();
    const el = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="stamp"]') as HTMLElement;

    expect(el.textContent?.trim()).toBe('');
    expect(el.hasAttribute('title')).toBe(false);
  });
});

describe('FleetWhen day-boundary rollover (review gate finding)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 18, 23, 45));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('rolls a mounted stamp from today into "Yesterday" once the clock crosses midnight, with no new `iso` input', async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.iso.set(new Date(2026, 6, 18, 23, 45).toISOString());
    fixture.detectChanges();
    const el = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="stamp"]') as HTMLElement;
    expect(el.textContent?.trim()).toBe('23:45');

    vi.setSystemTime(new Date(2026, 6, 19, 0, 5));
    await vi.advanceTimersByTimeAsync(60_000);
    fixture.detectChanges();

    expect(el.textContent?.trim()).toBe('Yesterday 23:45');
    // The tooltip is time-independent for a fixed `iso` — unaffected by the tick.
    expect(el.getAttribute('title')).toBe('2026/07/18 23:45:00');
  });

  it('drives every mounted instance off one shared timer', () => {
    vi.useFakeTimers();
    const before = vi.getTimerCount();
    const fixtures = [1, 2, 3].map(() => TestBed.createComponent(TestHost));
    fixtures.forEach((f) => f.detectChanges());

    expect(vi.getTimerCount()).toBe(before + 1);
  });
});
