import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { settle, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { GardeningProposalsPage } from './gardening-proposals-page';

/**
 * The garden proposal docket container's own `.gp-layout` two-column split
 * (`gardening-proposals-page.css`) — `gardening-routines-page.shell-sweep.spec.ts`'s
 * own real-Chromium proof that the `@media (max-width: 480px)` rule collapses the
 * grid to a single stacked column (`bzh:narrow-viewport-tier-rule`): jsdom parses the
 * query without ever evaluating it, and gardening sits in the hub's mobile bottom tab
 * bar, so the narrow width is load-bearing, not incidental.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const PROPOSAL = {
  proposal_id: 'gp_1',
  routine_name: 'comments',
  class: 'fix-the-source',
  title: 'Author a docstring standard',
  body: 'Seventeen modules narrate their own change history.',
  created_at: '2026-01-01T00:00:00Z',
  findings: ['fin_1'],
  closure: null,
};

async function render() {
  const stub = stubRequestClient(hubClient, (method, path) => {
    if (method === 'GET' && path === '/api/garden-proposals') return [PROPOSAL];
    return {};
  });
  await TestBed.configureTestingModule({
    imports: [GardeningProposalsPage],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningProposalsPage);
  await settle(fixture, 6);
  return { fixture, stub };
}

describe('gardening proposals page layout shell sweep (web:shell-sweep, blizzard#403)', () => {
  it('sits the list beside the panel above 480px, and stacks them at 390px and 320px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const { fixture, stub } = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(1280, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      let list = root.querySelector<HTMLElement>('.gp-list');
      let panel = root.querySelector<HTMLElement>('.gp-panel');
      expect(list, '1280px: no .gp-list in the DOM').not.toBeNull();
      expect(panel, '1280px: no .gp-panel in the DOM').not.toBeNull();
      expect(list!.getBoundingClientRect().top).toBe(panel!.getBoundingClientRect().top);
      expect(
        list!.getBoundingClientRect().right,
        '1280px: list and panel do not sit side by side',
      ).toBeLessThanOrEqual(panel!.getBoundingClientRect().left);

      for (const width of [390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        list = root.querySelector<HTMLElement>('.gp-list');
        panel = root.querySelector<HTMLElement>('.gp-panel');
        expect(list, `${width}px: no .gp-list in the DOM`).not.toBeNull();
        expect(panel, `${width}px: no .gp-panel in the DOM`).not.toBeNull();
        expect(
          list!.getBoundingClientRect().top,
          `${width}px: list and panel share a top — the grid did not collapse`,
        ).not.toBe(panel!.getBoundingClientRect().top);

        const layout = root.querySelector<HTMLElement>('.gp-layout')!;
        expect(
          layout.scrollWidth,
          `${width}px: layout overflows horizontally (${layout.scrollWidth} > ${layout.clientWidth})`,
        ).toBeLessThanOrEqual(layout.clientWidth);
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
