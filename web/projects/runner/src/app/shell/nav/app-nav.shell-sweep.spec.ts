import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { provideActiveRunner } from '../../core/active-runner';
import { AppNav } from './app-nav';

/**
 * The panel's top tab strip with its runner switcher — its own half of `web:shell-sweep`
 * (`blizzard-context:/verification/blizzard.md` bzh:web-shell-sweep). `KitTabStrip`/
 * `KitTab`'s chrome is unclamped flex, so this proves that the Host/Board/Events/Machine
 * labels and a host's worth of long-named, braked runners in the switcher never force the
 * strip past the viewport — the switcher's chips scroll inside it instead — and that every
 * tab stays on-screen, at every viewport from a wide monitor down to the narrowest common
 * phone.
 *
 * Excluded from the default `ng test runner` run (`angular.json`'s `test.exclude`) because
 * it needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const WIDTHS = [1400, 1024, 768, 640, 480, 390, 320];

/** Five runners — long names, one braked by both sides, one halted. */
const RUNNERS = {
  items: [
    { runner_id: 'rn_1', name: 'r-claude', state: 'serving', pause: { local: false, hub: false, effective: false } },
    {
      runner_id: 'rn_2',
      name: 'r-chatgpt-with-a-rather-long-runner-name',
      state: 'serving',
      pause: { local: true, hub: true, effective: true },
    },
    { runner_id: 'rn_3', name: 'r-gemini', state: 'serving', pause: { local: true, hub: false, effective: true, local_reason: 'usage limit' } },
    { runner_id: 'rn_4', name: 'r-local-experiments', state: 'serving' },
    { runner_id: null, name: 'r-broken', state: 'halted', halted_reasons: ['no hub token'] },
  ],
};

describe('runner app-nav shell sweep (web:shell-sweep)', () => {
  it('renders every tab and the runner switcher with no horizontal overflow at every width', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);
    const stub = stubRequestClient(runnerClient, (method, path) => (method === 'GET' && path === '/api/host/runners' ? RUNNERS : {}));

    await TestBed.configureTestingModule({
      imports: [AppNav],
      providers: [
        provideZonelessChangeDetection(),
        provideActiveRunner('rn_1'),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(AppNav);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await fixture.whenStable();

    try {
      for (const width of WIDTHS) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width=${width}`;
        const nav = root.querySelector<HTMLElement>('[data-testid="app-nav"]');
        expect(nav, `${label}: no tab strip in the DOM`).not.toBeNull();
        expect(
          root.querySelectorAll('[data-testid^="runner-switcher-"]').length,
          `${label}: the switcher does not list every runner`,
        ).toBe(RUNNERS.items.length);

        expect(
          nav!.scrollWidth,
          `${label}: the tab strip overflows horizontally (${nav!.scrollWidth} > ${window.innerWidth})`,
        ).toBeLessThanOrEqual(window.innerWidth);

        for (const testid of ['nav-host', 'nav-board', 'nav-events', 'nav-machine']) {
          const tab = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
          expect(tab, `${label}: no ${testid} tab`).not.toBeNull();
          expect(tab!.getBoundingClientRect().right, `${label}: ${testid}'s right edge is past the viewport`).toBeLessThanOrEqual(
            window.innerWidth,
          );
        }

        const switcher = root.querySelector<HTMLElement>('[data-testid="runner-switcher"]')!;
        expect(
          switcher.getBoundingClientRect().right,
          `${label}: the switcher's right edge is past the viewport`,
        ).toBeLessThanOrEqual(window.innerWidth);
      }
    } finally {
      root.remove();
      stub.restore();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
