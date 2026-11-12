import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { commands, page, userEvent } from 'vitest/browser';

import { FLEET_CLOCK, formatAbsolute } from 'fleet';
import { bounceView, chunkDetail, chunkSummary, FIXTURE_NOW, fixtureInstant, prView, settle, stubHubClient } from 'fleet/testing';
import { DeliveryPage } from './delivery-page';
import { DeliveryChunkPage } from './delivery-chunk-page';
import { DeliveryGlanceView } from './delivery-glance-view';
import { deliveryList } from './delivery.model';

/** The app shell bounds and clips the routed page; neither route may grow it to fit its history. */
@Component({
  selector: 'app-test-delivery-shell',
  imports: [RouterOutlet],
  template: '<router-outlet />',
  styles: `
    :host { display: flex; flex-direction: column; height: 600px; min-height: 0; overflow: hidden; }
    router-outlet { display: none; }
  `,
})
class TestDeliveryShell {}

const deliveryId = 'ch_delivery_scroll';
const deliveryPrs = Array.from({ length: 16 }, (_, index) => prView({
  repo: `org/repository-${index}`, number: index + 1, url: `https://example.test/repository-${index}/pull/${index + 1}`,
}));
const deliveryBounces = Array.from({ length: 60 }, (_, index) => bounceView({
  cause: 'failure', recorded_at: fixtureInstant(-(index + 1) * 60),
  envelope: JSON.stringify({ cause: 'failure', detail: `Delivery diagnostic ${index}: repository checks failed.` }),
}));

@Component({
  selector: 'app-test-delivery-glance-page',
  imports: [DeliveryGlanceView],
  template: '<app-delivery-glance-view state="ready" [list]="list" [now]="now" />',
})
class TestDeliveryGlancePage {
  readonly now = FIXTURE_NOW;
  readonly list = deliveryList({
    chunks: [chunkSummary({
      chunk_id: deliveryId, graph_id: 'gr_build', status: 'needs_human', bounce_count: 1,
      latest_bounce: bounceView({ cause: 'poll-timeout', envelope: '{}', recorded_at: fixtureInstant(-60) }),
    })],
    flows: [], repositories: [], facts: new Map(), workItems: new Map(), projects: [], runnerHosts: new Map(), now: FIXTURE_NOW,
  });
}

describe('Delivery glance diagnostic hit target shell sweep (web:shell-sweep)', () => {
  for (const width of [390, 320]) {
    it(`exposes the full diagnostic on the actual stage hit target and navigates normally at ${width}px`, async () => {
      await page.viewport(width, 800);
      TestBed.resetTestingModule();
      const tokens = document.createElement('style');
      tokens.textContent = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
      document.head.appendChild(tokens);
      const stub = stubHubClient({
        'GET /api/flows': () => ({ flows: [] }),
        'GET /api/repositories': () => ({ repositories: [] }),
        'GET /api/chunks/{chunk_id}': () => chunkDetail({
          chunk_id: deliveryId, graph_id: 'gr_build', status: 'needs_human', history: [],
          bounces: [bounceView({ cause: 'poll-timeout', envelope: '{}', recorded_at: fixtureInstant(-60) })],
        }),
      });
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      await TestBed.configureTestingModule({
        imports: [TestDeliveryShell],
        providers: [
          provideZonelessChangeDetection(), provideTanStackQuery(queryClient),
          provideRouter([
            { path: 'delivery/chunk/:chunkId', component: DeliveryChunkPage },
            { path: 'delivery', component: TestDeliveryGlancePage },
          ]),
          { provide: FLEET_CLOCK, useValue: () => FIXTURE_NOW },
        ],
      }).compileComponents();
      const fixture = TestBed.createComponent(TestDeliveryShell);
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      try {
        const router = TestBed.inject(Router);
        await router.navigateByUrl('/delivery');
        await settle(fixture, 12);
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const cards = root.querySelectorAll('[data-testid="delivery-card"]');
        expect(cards).toHaveLength(1);
        const stage = cards[0].querySelector<HTMLElement>('.stage')!;
        expect(stage.textContent?.trim()).toBe('poll-timeout');
        const rect = stage.getBoundingClientRect();
        expect(rect.width).toBeGreaterThan(0);
        expect(rect.height).toBeGreaterThan(0);
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        const anchor = cards[0].querySelector<HTMLAnchorElement>('a.hit')!;
        expect(hit, 'the stage center must hit the navigable card link').toBe(anchor);
        expect(anchor.title).toBe(`poll-timeout\n${formatAbsolute(fixtureInstant(-60))}`);
        expect(anchor.getAttribute('href')).toBe(`/delivery/chunk/${deliveryId}`);
        expect(stage.getAttribute('title')).toBeNull();
        await userEvent.hover(anchor);
        await userEvent.click(anchor);
        await settle(fixture, 12);
        expect(router.url).toBe(`/delivery/chunk/${deliveryId}`);
        expect(root.querySelector('[data-testid="delivery-chunk-page"]')).not.toBeNull();
        const diagnostic = root.querySelector('.compact-bounce');
        expect(diagnostic?.getAttribute('title')).toBe('poll-timeout');
        expect(diagnostic?.querySelector('.detail')?.textContent).toBe('poll-timeout');
      } finally {
        fixture.destroy();
        root.remove();
        queryClient.clear();
        stub.restore();
        tokens.remove();
      }
    });
  }
});

describe('Delivery desktop diagnostic hit target shell sweep (web:shell-sweep)', () => {
  it('exposes the full bounce on the selection hit without intercepting the PR link', async () => {
    await page.viewport(1280, 800);
    TestBed.resetTestingModule();
    const tokens = document.createElement('style');
    tokens.textContent = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
    document.head.appendChild(tokens);
    const detail = 'Repository checks failed: a long diagnostic with the complete account of the failed delivery and its underlying cause.';
    const bounce = bounceView({ cause: 'failure', envelope: JSON.stringify({ cause: 'failure', detail }), recorded_at: fixtureInstant(-60), withdrawn: true });
    const stub = stubHubClient({
      'GET /api/flows': () => ({ flows: [] }),
      'GET /api/repositories': () => ({ repositories: [] }),
      'GET /api/chunks': () => ({ chunks: [chunkSummary({ chunk_id: deliveryId, graph_id: 'gr_build', status: 'needs_human', bounce_count: 1, latest_bounce: bounce, open_prs: [deliveryPrs[0]] })], next_cursor: null, board_done_policy: null }),
      'GET /api/work-items': () => ({ chunks: {} }),
      'GET /api/chunks/{chunk_id}': () => chunkDetail({ chunk_id: deliveryId, graph_id: 'gr_build', status: 'needs_human', history: [], bounces: [bounce] }),
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await TestBed.configureTestingModule({
      imports: [TestDeliveryShell],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(queryClient), provideRouter([{ path: 'delivery', component: DeliveryPage }]), { provide: FLEET_CLOCK, useValue: () => FIXTURE_NOW }],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestDeliveryShell);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    try {
      const router = TestBed.inject(Router);
      await router.navigateByUrl('/delivery');
      await settle(fixture, 12);
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const row = root.querySelector<HTMLElement>('[data-testid="delivery-row"]')!;
      const diagnostic = row.querySelector<HTMLElement>('[data-testid="delivery-bounce"]')!;
      const rect = diagnostic.getBoundingClientRect();
      expect(rect.width).toBeGreaterThan(0);
      expect(rect.height).toBeGreaterThan(0);
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2) as HTMLElement;
      expect(hit).toBe(row.querySelector('[data-testid="delivery-row-open"]'));
      expect(hit.tagName).toBe('BUTTON');
      expect(hit.getAttribute('title')).toBeNull();
      expect(hit.closest('[title]')?.getAttribute('title')).toBe(`${detail}\nwithdrawn\n${formatAbsolute(fixtureInstant(-60))}`);

      const link = row.querySelector<HTMLAnchorElement>('[data-testid="delivery-link"]')!;
      const linkRect = link.getBoundingClientRect();
      const linkHit = document.elementFromPoint(linkRect.left + linkRect.width / 2, linkRect.top + linkRect.height / 2)!;
      expect(link.contains(linkHit)).toBe(true);
      expect(link.href).toBe(deliveryPrs[0].url);
      expect(link.target).toBe('_blank');
      let clickedUrl: string | undefined;
      link.addEventListener('click', (event) => { clickedUrl = link.href; event.preventDefault(); }, { once: true });
      await userEvent.click(linkHit);
      await settle(fixture, 12);
      expect(clickedUrl).toBe(deliveryPrs[0].url);
      expect(router.url).toBe('/delivery');
      expect(root.querySelector('app-delivery-drawer-view')).toBeNull();
      await userEvent.click(hit);
      await settle(fixture, 12);
      expect(router.url).toContain(`chunk=${deliveryId}`);
      expect(root.querySelector('app-delivery-drawer-view > .drawer')).not.toBeNull();
    } finally {
      fixture.destroy();
      root.remove();
      queryClient.clear();
      stub.restore();
      tokens.remove();
    }
  });
});

describe('Delivery drawer and full-screen scroll shell sweep (web:shell-sweep)', () => {
  for (const composition of ['drawer', 'screen'] as const) {
    for (const width of [1280, 901, 900, 390, 320]) {
      it(`${composition} keeps the last history entry reachable with bounded columns at ${width}px`, async () => {
        await page.viewport(width, 800);
        TestBed.resetTestingModule();
        const tokens = document.createElement('style');
        tokens.textContent = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
        document.head.appendChild(tokens);
        const stub = stubHubClient({
          'GET /api/flows': () => ({ flows: [] }),
          'GET /api/repositories': () => ({ repositories: [] }),
          'GET /api/chunks': () => ({ chunks: [chunkSummary({ chunk_id: deliveryId, graph_id: 'gr_build', graph_name: 'build', status: 'needs_human', open_prs: [deliveryPrs[0]] })], next_cursor: null, board_done_policy: null }),
          'GET /api/work-items': () => ({ chunks: {} }),
          'GET /api/chunks/{chunk_id}': () => chunkDetail({
            chunk_id: deliveryId, graph_id: 'gr_build', status: 'needs_human', history: [], flow_record: [], flow_places: [],
            open_prs: deliveryPrs, bounces: deliveryBounces,
          }),
        });
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        await TestBed.configureTestingModule({
          imports: [TestDeliveryShell],
          providers: [
            provideZonelessChangeDetection(),
            provideTanStackQuery(queryClient),
            provideRouter([
              { path: 'delivery/chunk/:chunkId', component: DeliveryChunkPage },
              { path: 'delivery', component: DeliveryPage },
            ]),
            { provide: FLEET_CLOCK, useValue: () => FIXTURE_NOW },
          ],
        }).compileComponents();
        const fixture = TestBed.createComponent(TestDeliveryShell);
        const root = fixture.nativeElement as HTMLElement;
        document.body.appendChild(root);
        try {
          await TestBed.inject(Router).navigateByUrl(composition === 'drawer' ? `/delivery?chunk=${deliveryId}` : `/delivery/chunk/${deliveryId}`);
          await settle(fixture, 12);
          await new Promise((resolve) => requestAnimationFrame(resolve));
          const drawer = root.querySelector<HTMLElement>('app-delivery-drawer-view > .drawer')!;
          expect(drawer, 'real drawer view did not render').not.toBeNull();
          const side = drawer.querySelector<HTMLElement>('.body > .side')!;
          const main = drawer.querySelector<HTMLElement>('.body > .main')!;
          const repos = [...side.querySelectorAll<HTMLElement>('[data-testid="delivery-drawer-repo"]')];
          const history = [...main.querySelectorAll<HTMLElement>('[data-testid="delivery-history-entry"]')];
          expect(repos).toHaveLength(deliveryPrs.length);
          expect(history.map((entry) => entry.querySelector('.compact-bounce .detail')?.textContent?.trim())).toEqual(
            deliveryBounces.map((_, index) => `Delivery diagnostic ${index}: repository checks failed.`),
          );
          const sideRect = side.getBoundingClientRect();
          const mainRect = main.getBoundingClientRect();
          const stacked = composition === 'screen' || width <= 900;
          if (stacked) {
            expect(mainRect.top).toBeGreaterThanOrEqual(sideRect.bottom - 1);
            expect(Math.abs(mainRect.left - sideRect.left)).toBeLessThanOrEqual(1);
            expect(getComputedStyle(side).overflowY).toBe('visible');
            expect(getComputedStyle(main).overflowY).toBe('visible');
          } else {
            expect(mainRect.left).toBeGreaterThanOrEqual(sideRect.right - 1);
            expect(Math.abs(mainRect.top - sideRect.top)).toBeLessThanOrEqual(1);
            expect(getComputedStyle(side).overflowY).toBe('auto');
            expect(side.scrollHeight).toBeGreaterThan(side.clientHeight);
          }
          for (const box of [root, drawer, side, main]) {
            expect(box.scrollWidth, `${box.className} overflows horizontally`).toBeLessThanOrEqual(box.clientWidth + 1);
            expect(box.getBoundingClientRect().left).toBeGreaterThanOrEqual(-1);
            expect(box.getBoundingClientRect().right).toBeLessThanOrEqual(width + 1);
          }
          expect(drawer.getBoundingClientRect().bottom).toBeLessThanOrEqual(root.getBoundingClientRect().bottom + 1);
          expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
          const scroller = stacked ? drawer : main;
          expect(getComputedStyle(scroller).overflowY).toBe('auto');
          expect(scroller.scrollHeight).toBeGreaterThan(scroller.clientHeight);
          // A descendant's existence is insufficient: scroll the production owner and hit-test the last row.
          scroller.scrollTop = scroller.scrollHeight;
          await new Promise((resolve) => requestAnimationFrame(resolve));
          expect(scroller.scrollTop).toBeGreaterThan(0);
          const last = history[history.length - 1];
          const lastRect = last.getBoundingClientRect();
          const scrollRect = scroller.getBoundingClientRect();
          expect(lastRect.height).toBeGreaterThan(0);
          expect(lastRect.top).toBeGreaterThanOrEqual(scrollRect.top - 1);
          expect(lastRect.bottom).toBeLessThanOrEqual(scrollRect.bottom + 1);
          expect(lastRect.bottom).toBeLessThanOrEqual(root.getBoundingClientRect().bottom + 1);
          const hit = document.elementFromPoint(lastRect.left + lastRect.width / 2, lastRect.top + lastRect.height / 2);
          expect(hit !== null && last.contains(hit), 'last history row is clipped or covered').toBe(true);
          if (composition === 'screen') {
            expect(root.querySelector('[data-testid="delivery-chunk-back"]')).not.toBeNull();
            expect(root.querySelector('[data-testid="detail-close"]')).toBeNull();
          }
          const prLink = repos[repos.length - 1].querySelector<HTMLAnchorElement>('a')!;
          expect(prLink.href).toBe(deliveryPrs[deliveryPrs.length - 1].url);
        } finally {
          fixture.destroy();
          root.remove();
          queryClient.clear();
          stub.restore();
          tokens.remove();
        }
      });
    }
  }
});
