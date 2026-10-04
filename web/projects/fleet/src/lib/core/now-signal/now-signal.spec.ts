import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { injectNowSignal } from './now-signal';

@Component({
  selector: 'fleet-test-host',
  template: ``,
})
class TestHost {
  readonly now = injectNowSignal(1000);
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
    vi.useRealTimers();
  });

  it('advances the returned signal on the tick alone, with no input change', () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.detectChanges();
    const first = fixture.componentInstance.now();

    vi.advanceTimersByTime(1000);
    const second = fixture.componentInstance.now();

    expect(second).toBeGreaterThan(first);
  });

  it('clears its interval when the host is destroyed', () => {
    const before = vi.getTimerCount();
    const fixture = TestBed.createComponent(TestHost);
    fixture.detectChanges();

    expect(vi.getTimerCount()).toBeGreaterThan(before);
    fixture.destroy();
    expect(vi.getTimerCount()).toBe(before);
  });
});
