import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { FLEET_CLOCK } from 'fleet';
import { injectHubClock } from './hub-clock';

const BROWSER = Date.parse('2026-07-13T12:00:00Z');

@Component({
  selector: 'app-test-hub-clock-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestHubClockHost {
  readonly answeredAt = signal(0);
  readonly now = injectHubClock(() => this.answeredAt());
}

describe('injectHubClock', () => {
  let browser = BROWSER;
  afterEach(() => vi.useRealTimers());

  function mount() {
    // Only the shared clock's interval is faked; the browser clock is the stub below.
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    browser = BROWSER;
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), { provide: FLEET_CLOCK, useValue: () => browser }],
    });
    const fixture = TestBed.createComponent(TestHubClockHost);
    fixture.detectChanges();
    return fixture;
  }

  it('is the browser clock before the hub has answered', () => {
    const fixture = mount();
    expect(fixture.componentInstance.now()).toBe(BROWSER);
  });

  it('shifts onto the hub clock once it answers, and ticks on from there', () => {
    const fixture = mount();
    fixture.componentInstance.answeredAt.set(BROWSER + 90_000);
    fixture.detectChanges();
    expect(fixture.componentInstance.now()).toBe(BROWSER + 90_000);

    browser += 5_000;
    vi.advanceTimersByTime(5_000);
    expect(fixture.componentInstance.now()).toBe(BROWSER + 95_000);
  });

  it('re-reads the skew on each answer', () => {
    const fixture = mount();
    fixture.componentInstance.answeredAt.set(BROWSER + 90_000);
    fixture.detectChanges();
    browser += 10_000;
    vi.advanceTimersByTime(1_000);
    fixture.componentInstance.answeredAt.set(BROWSER - 20_000);
    fixture.detectChanges();

    expect(fixture.componentInstance.now()).toBe(BROWSER - 20_000);
  });
});
