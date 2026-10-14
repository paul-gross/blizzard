import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { provideActiveRunner } from '../../core/active-runner';
import { RunnerSwitcher } from './runner-switcher';

/**
 * The runner switcher in the mobile chrome (`variant="mobile"`, the row the shell mounts
 * under the titlebar) — its own half of `web:shell-sweep`
 * (`blizzard-context:/verification/blizzard.md` bzh:web-shell-sweep). Proves that a host's
 * worth of long-named, braked runners scroll inside the row rather than widen it, that the
 * active runner's chip stays hit-testable, at phone widths and up to a desktop width.
 *
 * Excluded from the default `ng test runner` run (`angular.json`'s `test.exclude`) because
 * it needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const WIDTHS = [1280, 768, 390, 320];

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
    { runner_id: null, name: 'r-broken', state: 'halted', halted_reasons: ['no hub token'] },
  ],
};

describe('runner switcher mobile shell sweep (web:shell-sweep)', () => {
  it('keeps the row inside the viewport with its chips scrolling inside it, at every width', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);
    const stub = stubRequestClient(runnerClient, (method, path) => (method === 'GET' && path === '/api/host/runners' ? RUNNERS : {}));

    await TestBed.configureTestingModule({
      imports: [RunnerSwitcher],
      providers: [
        provideZonelessChangeDetection(),
        provideActiveRunner('rn_1'),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(RunnerSwitcher);
    fixture.componentRef.setInput('variant', 'mobile');
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
        const switcher = root.querySelector<HTMLElement>('[data-testid="runner-switcher"]');
        expect(switcher, `${label}: no switcher in the DOM`).not.toBeNull();
        const rect = switcher!.getBoundingClientRect();
        expect(rect.left, `${label}: the switcher's left edge is off-viewport`).toBeGreaterThanOrEqual(0);
        expect(rect.right, `${label}: the switcher's right edge is past the viewport`).toBeLessThanOrEqual(window.innerWidth);
        expect(root.scrollWidth, `${label}: the row overflows horizontally`).toBeLessThanOrEqual(window.innerWidth);

        const active = root.querySelector<HTMLElement>('[data-testid="runner-switcher-rn_1"]')!;
        const activeRect = active.getBoundingClientRect();
        expect(activeRect.width, `${label}: the active chip has zero width`).toBeGreaterThan(0);
        const hit = document.elementFromPoint(activeRect.left + activeRect.width / 2, activeRect.top + activeRect.height / 2);
        expect(active.contains(hit), `${label}: the active chip's center hit-tests outside it`).toBe(true);
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
