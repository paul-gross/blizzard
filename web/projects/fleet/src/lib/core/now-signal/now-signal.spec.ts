import { DOCUMENT } from '@angular/common';
import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { FLEET_CLOCK, injectNowSignal } from './now-signal';

@Component({
  selector: 'fleet-test-host',
  template: ``,
})
class TestHost {
  readonly now = injectNowSignal();
}

function setVisibility(state: 'visible' | 'hidden'): void {
  const doc = TestBed.inject(DOCUMENT);
  Object.defineProperty(doc, 'visibilityState', { configurable: true, get: () => state });
  doc.dispatchEvent(new Event('visibilitychange'));
}

describe('injectNowSignal', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    });
  });

  afterEach(() => {
    TestBed.resetTestingModule();
    vi.useRealTimers();
  });

  it('advances the returned signal on the tick alone, with no input change', () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.detectChanges();
    const first = fixture.componentInstance.now();

    vi.advanceTimersByTime(1000);

    expect(fixture.componentInstance.now()).toBeGreaterThan(first);
  });

  it('reads its initial value and every tick through FLEET_CLOCK', () => {
    let clock = 5_000;
    TestBed.overrideProvider(FLEET_CLOCK, { useValue: () => clock });
    const fixture = TestBed.createComponent(TestHost);
    fixture.detectChanges();
    expect(fixture.componentInstance.now()).toBe(5_000);

    clock = 9_000;
    vi.advanceTimersByTime(1000);
    expect(fixture.componentInstance.now()).toBe(9_000);
  });

  it('shares one timer across every host', () => {
    const before = vi.getTimerCount();
    const a = TestBed.createComponent(TestHost);
    const b = TestBed.createComponent(TestHost);
    a.detectChanges();
    b.detectChanges();

    expect(vi.getTimerCount()).toBe(before + 1);
    expect(a.componentInstance.now()).toBe(b.componentInstance.now());
  });

  it('keeps the timer running when a host is destroyed, and clears it with the root injector', () => {
    const before = vi.getTimerCount();
    const fixture = TestBed.createComponent(TestHost);
    fixture.detectChanges();
    fixture.destroy();
    expect(vi.getTimerCount()).toBe(before + 1);

    TestBed.resetTestingModule();
    expect(vi.getTimerCount()).toBe(before);
  });

  it('stops ticking while hidden and re-reads immediately on becoming visible', () => {
    let clock = 1_000;
    TestBed.overrideProvider(FLEET_CLOCK, { useValue: () => clock });
    const fixture = TestBed.createComponent(TestHost);
    fixture.detectChanges();

    setVisibility('hidden');
    clock = 4_000;
    vi.advanceTimersByTime(3000);
    expect(fixture.componentInstance.now()).toBe(1_000);

    clock = 8_000;
    setVisibility('visible');
    expect(fixture.componentInstance.now()).toBe(8_000);

    clock = 9_000;
    vi.advanceTimersByTime(1000);
    expect(fixture.componentInstance.now()).toBe(9_000);
  });
});
