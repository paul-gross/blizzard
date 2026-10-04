import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { matchesMobileViewport } from './matches-mobile-viewport';
import { ViewportService } from './viewport-service';

describe('matchesMobileViewport', () => {
  beforeEach(() => {
    localStorage.clear();
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
  });

  it('matches when ViewportService.mode is mobile', () => {
    TestBed.inject(ViewportService).setOverride('mobile');

    const matched = TestBed.runInInjectionContext(() =>
      matchesMobileViewport(undefined as never, [undefined as never]),
    );

    expect(matched).toBe(true);
  });

  it('declines to match when ViewportService.mode is desktop', () => {
    TestBed.inject(ViewportService).setOverride('desktop');

    const matched = TestBed.runInInjectionContext(() =>
      matchesMobileViewport(undefined as never, [undefined as never]),
    );

    expect(matched).toBe(false);
  });
});
