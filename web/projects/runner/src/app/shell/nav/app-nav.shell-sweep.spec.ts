import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { AppNav } from './app-nav';

/**
 * The panel's top tab strip with its filters — its own half of `web:shell-sweep`
 * (`blizzard-context:/verification/blizzard.md` bzh:web-shell-sweep). `KitTabStrip`/
 * `KitTab`'s chrome is unclamped flex, so this proves that the Board/Events/Host labels and
 * the Runner and Project filters over a host's worth of long-named, braked runners and
 * several projects never force the strip past the viewport — each filter's chips scroll
 * inside it instead — and that every tab stays on-screen, at every viewport from a wide
 * monitor down to the narrowest common phone.
 *
 * Excluded from the default `ng test runner` run (`angular.json`'s `test.exclude`) because
 * it needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const WIDTHS = [1400, 1024, 768, 640, 480, 390, 320];

/** Five runners — long names, one braked by both sides, one braked here, one halted. */
const RUNNERS = {
  items: [
    {
      runner_id: 'rn_1',
      name: 'r-claude',
      state: 'serving',
      pause: { local: false, hub: false, effective: false, local_reason: null },
      bindings: [{ project: 'blizzard', workspace_id: 'ws', agents: 4 }],
    },
    {
      runner_id: 'rn_2',
      name: 'r-chatgpt-with-a-rather-long-runner-name',
      state: 'serving',
      pause: { local: true, hub: true, effective: true, local_reason: null },
      bindings: [{ project: 'winter', workspace_id: 'ws', agents: 2 }],
    },
    {
      runner_id: 'rn_3',
      name: 'r-gemini',
      state: 'serving',
      pause: { local: true, hub: false, effective: true, local_reason: 'usage limit' },
      bindings: [{ project: 'console-with-a-long-project-slug', workspace_id: 'ws', agents: 2 }],
    },
    {
      runner_id: 'rn_4',
      name: 'r-local-experiments',
      state: 'serving',
      bindings: [{ project: 'ops', workspace_id: 'ws', agents: 1 }],
    },
    { runner_id: null, name: 'r-broken', state: 'halted', halted_reasons: ['no hub token'], bindings: [] },
  ],
};

/** All, then each runner's one bound project — the dashboards are empty, so the bindings name them. */
const PROJECT_CHOICES = 1 + 4;

describe('runner app-nav shell sweep (web:shell-sweep)', () => {
  it('renders every tab and both filters with no horizontal overflow at every width', async () => {
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
          root.querySelectorAll('[data-testid="runner-filter"] button').length,
          `${label}: the Runner filter does not offer All and every runner`,
        ).toBe(1 + RUNNERS.items.length);
        expect(
          root.querySelectorAll('[data-testid="project-filter"] button').length,
          `${label}: the Project filter does not offer All and every project`,
        ).toBe(PROJECT_CHOICES);

        expect(
          nav!.scrollWidth,
          `${label}: the tab strip overflows horizontally (${nav!.scrollWidth} > ${window.innerWidth})`,
        ).toBeLessThanOrEqual(window.innerWidth);

        for (const testid of ['nav-board', 'nav-events', 'nav-host']) {
          const tab = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
          expect(tab, `${label}: no ${testid} tab`).not.toBeNull();
          const rect = tab!.getBoundingClientRect();
          expect(rect.width, `${label}: ${testid} has zero width`).toBeGreaterThan(0);
          expect(rect.right, `${label}: ${testid}'s right edge is past the viewport`).toBeLessThanOrEqual(window.innerWidth);
        }

        for (const testid of ['runner-filter', 'project-filter']) {
          const filter = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!;
          expect(
            filter.getBoundingClientRect().right,
            `${label}: the ${testid}'s right edge is past the viewport`,
          ).toBeLessThanOrEqual(window.innerWidth);
        }
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
