import { BreakpointObserver, type BreakpointState } from '@angular/cdk/layout';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { vi } from 'vitest';

import { ViewportService } from './viewport-service';

const STORAGE_KEY = 'blizzard.viewport.override';

/** Minimal `BreakpointObserver` stand-in — drives the `(max-width: …)` match
 * deterministically instead of depending on jsdom's real viewport. */
class FakeBreakpointObserver {
  matches = false;
  readonly changes = new Subject<BreakpointState>();

  isMatched(): boolean {
    return this.matches;
  }

  observe(): Subject<BreakpointState> {
    return this.changes;
  }
}

function setUp(): { service: ViewportService; breakpoint: FakeBreakpointObserver } {
  const breakpoint = new FakeBreakpointObserver();
  TestBed.configureTestingModule({
    providers: [provideZonelessChangeDetection(), { provide: BreakpointObserver, useValue: breakpoint }],
  });
  const service = TestBed.inject(ViewportService);
  return { service, breakpoint };
}

describe('ViewportService', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults to auto and follows the breakpoint-derived mode', () => {
    const { service, breakpoint } = setUp();

    expect(service.override()).toBe('auto');
    expect(service.mode()).toBe('desktop');

    breakpoint.changes.next({ matches: true, breakpoints: {} });
    expect(service.mode()).toBe('mobile');

    breakpoint.changes.next({ matches: false, breakpoints: {} });
    expect(service.mode()).toBe('desktop');
  });

  it('reads the initial breakpoint match synchronously, before any emission', () => {
    const breakpoint = new FakeBreakpointObserver();
    breakpoint.matches = true;
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), { provide: BreakpointObserver, useValue: breakpoint }],
    });

    expect(TestBed.inject(ViewportService).mode()).toBe('mobile');
  });

  it('an override takes precedence over the breakpoint-derived mode', () => {
    const { service, breakpoint } = setUp();
    breakpoint.changes.next({ matches: true, breakpoints: {} });
    expect(service.mode()).toBe('mobile');

    service.setOverride('desktop');
    expect(service.override()).toBe('desktop');
    expect(service.mode()).toBe('desktop');

    breakpoint.changes.next({ matches: false, breakpoints: {} });
    expect(service.mode()).toBe('desktop');

    service.setOverride('auto');
    expect(service.mode()).toBe('desktop');
  });

  it('persists the override to localStorage and restores it on next construction', () => {
    const { service } = setUp();
    service.setOverride('mobile');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('mobile');

    // A fresh injector, simulating the next page load reading the same localStorage.
    TestBed.resetTestingModule();
    const { service: restarted } = setUp();
    expect(restarted.override()).toBe('mobile');
    expect(restarted.mode()).toBe('mobile');
  });

  it('ignores a corrupt stored value and falls back to auto', () => {
    localStorage.setItem(STORAGE_KEY, 'not-a-real-mode');
    const { service } = setUp();
    expect(service.override()).toBe('auto');
  });

  it('degrades to in-memory only when localStorage throws', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });

    const { service } = setUp();
    expect(() => service.setOverride('mobile')).not.toThrow();
    expect(service.override()).toBe('mobile');

    setItem.mockRestore();
  });

  it('falls back to auto when localStorage.getItem throws during construction', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled');
    });

    const { service } = setUp();
    expect(service.override()).toBe('auto');

    getItem.mockRestore();
  });
});
