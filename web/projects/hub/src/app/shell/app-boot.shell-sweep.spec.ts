import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { EVENT_SOURCE_FACTORY, ViewportService, provideFlags } from 'fleet/shell';
import { OPERATOR_ME_RESPONSE, chunkCountsView, fleetSpendView, settle, stubHubClient } from 'fleet/testing';
import { page } from 'vitest/browser';
import { hubFlagsRead } from '../core/flags.query';
import { App } from './app';

@Component({
  selector: 'app-boot-test-content',
  template: '<div data-testid="boot-test-content">Ready</div>',
  styles: ':host { display: block; flex: 1; min-height: 0; }',
})
class TestContent {}

describe('app boot chrome (web:shell-sweep)', () => {
  for (const width of [390, 320]) {
    it(`holds navigation at the bottom through identity loading and ready content at ${width}px`, async () => {
      await page.viewport(width, 844);
      sessionStorage.clear();
      localStorage.clear();
      let release!: (me: typeof OPERATOR_ME_RESPONSE) => void;
      const identity = new Promise<typeof OPERATOR_ME_RESPONSE>((resolve) => { release = resolve; });
      const stub = stubHubClient({
        'GET /api/me': () => identity,
        'GET /api/auth/providers': () => [],
        'GET /api/flags': () => ({ flags: {}, overridden: [] }),
        'GET /api/health': () => ({ status: 'ok', service: 'blizzard-hub', version: '0.0.0', overridden: [] }),
        'GET /api/chunk-counts': () => chunkCountsView(),
        'GET /api/spend': () => fleetSpendView(),
        'GET /api/projects': () => ({ projects: [] }),
      });
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      await TestBed.configureTestingModule({
        imports: [App],
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(client),
          provideFlags(hubFlagsRead),
          provideRouter([{ path: '**', component: TestContent }]),
          { provide: EVENT_SOURCE_FACTORY, useValue: () => ({
            addEventListener: () => undefined,
            close: () => undefined,
          }) },
        ],
      }).compileComponents();
      TestBed.inject(ViewportService).setOverride('mobile');
      const fixture = TestBed.createComponent(App);
      const root = fixture.nativeElement as HTMLElement;
      root.style.cssText = 'display:block;height:844px';
      document.body.appendChild(root);
      try {
        await TestBed.inject(Router).navigateByUrl('/p/blizzard/events/-1');
        await settle(fixture);
        const bootNav = root.querySelector<HTMLElement>('nav[aria-label="Navigation loading"]')!;
        expect(bootNav).not.toBeNull();
        expect(bootNav.querySelectorAll('a,button')).toHaveLength(0);
        expect(bootNav.querySelector('.active')?.textContent?.trim()).toBe('Events');
        expect(root.querySelector('.boot-mark')).not.toBeNull();
        const before = bootNav.getBoundingClientRect();
        expect(before.height).toBeGreaterThanOrEqual(50);
        expect(before.bottom).toBeCloseTo(root.getBoundingClientRect().bottom, 0);

        release(OPERATOR_ME_RESPONSE);
        await settle(fixture);
        const nav = root.querySelector<HTMLElement>('[data-testid="mobile-tab-bar"]')!;
        expect(root.querySelector('[data-testid="boot-test-content"]')).not.toBeNull();
        expect(nav.getBoundingClientRect().top).toBeCloseTo(before.top, 0);
        expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
      } finally {
        release(OPERATOR_ME_RESPONSE);
        fixture.destroy();
        root.remove();
        client.clear();
        stub.restore();
        localStorage.clear();
        sessionStorage.clear();
      }
    });
  }
});
