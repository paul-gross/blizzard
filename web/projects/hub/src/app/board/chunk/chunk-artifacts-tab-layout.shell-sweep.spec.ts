import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient, type hubApi } from 'fleet';
import { OPERATOR_ME_RESPONSE, stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { ChunkPage } from './chunk-page';

/**
 * The Artifacts tab's real composed chain — `ChunkPage` → `ChunkArtifactsTab` →
 * `ChunkArtifactsPanel` — under a real browser (review M1/G16). `ChunkArtifactsPanel`'s
 * `.art-tab` sizes itself with `height: 100%`, a claim jsdom parses but never lays out —
 * `web:unit-test` cannot see whether that percentage actually resolves against a definite
 * containing block, only that the rule exists. Driven through a real router the way
 * `chunk-page-layout.shell-sweep.spec.ts`'s own composed-chain cases drive `ChunkPage`, so
 * `ChunkArtifactsTab`'s real host is genuinely in the chain, not stood in for.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it
 * needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */

/** Pump change detection until `ready()` holds, without `settle()`'s `whenStable()` —
 * `chunk-page-layout.shell-sweep.spec.ts`'s own `pumpUntil` (review G11/G12): a query
 * enabled only once an earlier one resolves registers a pending task Angular's zoneless
 * stability never retires, so `whenStable()` waits forever even once the DOM is settled. */
async function pumpUntil(fixture: { detectChanges(): void }, ready: () => boolean, tries = 60): Promise<void> {
  for (let i = 0; i < tries; i += 1) {
    fixture.detectChanges();
    if (ready()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  fixture.detectChanges();
  if (!ready()) throw new Error('pumpUntil: the awaited content never rendered');
}

const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

/** Enough distinct artifact rows to overflow `.art-nav`'s own box at any viewport this
 * sweep uses — the load-bearing fixture for "the nav list actually scrolls" rather than
 * merely rendering short enough to never need to. */
function overflowingArtifacts(): hubApi.ArtifactView[] {
  return Array.from({ length: 40 }, (_, i) => ({
    key: `build.artifact-${i}.1`,
    kind: 'asset',
    name: `artifact-${i}`,
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 1,
    content: `artifact ${i}'s own content, long enough to read as real body text`,
    recorded_at: `2026-08-09T00:${String(i).padStart(2, '0')}:00.000Z`,
  }));
}

const CHAIN_DETAIL: hubApi.ChunkDetail = {
  chunk_id: CHUNK_ID,
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_build',
  current_node_name: 'build',
  latest_epoch: 1,
  status: 'running',
  work_refs: [],
  history: [],
  artifacts: overflowingArtifacts(),
};

@Component({ selector: 'app-board-stub', template: '' })
class ChainBoardStub {}

const CHAIN_ROUTES = [
  { path: 'board', component: ChainBoardStub },
  { path: 'board/chunk/:chunkId', component: ChunkPage },
];

/** Stands in for `App`'s own `.layout` (`app.ts`) — the real height-capped, flex-column
 * ancestor that gives a routed page's `:host { flex: 1; min-height: 0 }` a definite height
 * to resolve against, the same helper `chunk-page-layout.shell-sweep.spec.ts` defines for
 * its own composed-chain cases. */
function mountInAppShell(root: HTMLElement): void {
  root.style.cssText = 'display: flex; flex-direction: column; height: 100%; min-height: 0; overflow: hidden;';
  document.body.appendChild(root);
}

describe('chunk page Artifacts tab composed-chain layout shell sweep (web:shell-sweep, review M1)', () => {
  it('keeps the nav list scrollable, bounded by the tab’s real box, not clipped with no scroll container', async () => {
    const stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/me') return OPERATOR_ME_RESPONSE;
      if (method === 'GET' && path.endsWith('/work-items')) return { items: [] };
      return CHAIN_DETAIL;
    });

    try {
      await TestBed.configureTestingModule({
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
          provideRouter(CHAIN_ROUTES),
        ],
      }).compileComponents();
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=artifacts`);
      const root = harness.fixture.nativeElement as HTMLElement;
      mountInAppShell(root);
      await pumpUntil(harness.fixture, () => root.querySelector('[data-testid="artifacts-tab-nav"]') !== null);

      try {
        await page.viewport(390, 700);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const tab = root.querySelector<HTMLElement>('app-chunk-artifacts-tab');
        const nav = root.querySelector<HTMLElement>('[data-testid="artifacts-tab-nav"]');
        expect(tab, 'no app-chunk-artifacts-tab in the composed chain').not.toBeNull();
        expect(nav, 'no artifacts-tab-nav in the DOM').not.toBeNull();

        // The regression's own signature: under the bug, `.art-tab`'s containing block
        // (the panel host) has no definite height, so `height: 100%` computes to `auto`
        // and the whole tab grows to fit every row rather than being clipped to the space
        // `.cps-body` actually has — this bounds it to the real viewport.
        expect(
          tab!.getBoundingClientRect().height,
          `the tab's own box is unbounded (${tab!.getBoundingClientRect().height}px) — the flex/height chain never reached it`,
        ).toBeLessThanOrEqual(700);

        // With a real, bounded box, the nav list is the one that overflows — and is a
        // genuine scroll container an operator can actually reach the last row through.
        expect(
          nav!.scrollHeight,
          'the nav list never overflows its own box — the 40-artifact fixture is not actually long enough',
        ).toBeGreaterThan(nav!.clientHeight);
        nav!.scrollTop = nav!.scrollHeight;
        expect(nav!.scrollTop, 'the nav list is clipped, not scrollable').toBeGreaterThan(0);
      } finally {
        root.remove();
      }
    } finally {
      stub.restore();
    }
  });
});
