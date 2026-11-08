import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  chunkCountsView,
  chunkDetail,
  chunkSummary,
  chunkUsageTotalView,
  fleetSpendView,
  OPERATOR_ME_RESPONSE,
  stubHubClient,
} from 'fleet/testing';
import { page } from 'vitest/browser';

import { BoardPage } from './board-page';

/**
 * The board page's chunk dock, under a real browser (`web:shell-sweep`): with nothing
 * selected the dock is mounted but takes no box, and the board runs the centre column's
 * full height; selecting a chunk opens the dock beneath the board, keeping the board's
 * left, top and width and shortening it; dismissing the selection restores the board's
 * at-rest box exactly. jsdom lays nothing out, so `web:unit-test` can assert only the
 * dock's `closed` class, never the geometry it produces (`board-page.css`'s `.board` and
 * `.dock` flex split and `.dock.closed`'s `display: none`).
 *
 * Open, the dock's kit panel fills the dock's share, and each of its three columns
 * scrolls on its own: a column given more content than it can show scrolls without
 * moving its siblings or growing the dock.
 *
 * Proven able to fail by dropping `.dock.closed`'s `display: none`: the closed dock then
 * takes room, and the at-rest board no longer runs the column's full height. The
 * open-dock claims are proven able to fail by dropping the panel body frame's
 * definite height (`chunk-detail-panel.css`'s `.d-frame`): the columns then grow to
 * their content and stop scrolling on their own.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */

const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYNDOCK';
const CHUNK = chunkSummary({ chunk_id: CHUNK_ID, graph_id: 'gr_1', status: 'running', current_node_id: 'nd_build', work_refs: [] });

const NO_TOKENS = { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0 };
const NO_SPEND = chunkUsageTotalView({ ...NO_TOKENS, cost_usd: 0, estimated_cost_usd: null, cost_partial: false });

const ROUTES: Parameters<typeof stubHubClient>[0] = {
  'GET /api/me': () => OPERATOR_ME_RESPONSE,
  'GET /api/chunks': () => ({ chunks: [CHUNK], next_cursor: null, board_done_policy: null }),
  'GET /api/chunks/{chunk_id}': () =>
    chunkDetail({
      ...CHUNK,
      graph_name: 'default',
      latest_epoch: 1,
      cost: NO_SPEND,
      history: [],
      artifacts: [],
      open_prs: [],
      closed_prs: [],
      landed_repos: [],
    }),
  'GET /api/chunks/{chunk_id}/work-items': () => ({ items: [] }),
  'GET /api/work-items': () => ({ chunks: {} }),
  'GET /api/queue': () => ({ entries: [], next_cursor: null }),
  'GET /api/backlog': () => ({ entries: [], next_cursor: null }),
  'GET /api/spend': () => fleetSpendView({ ...NO_TOKENS, cost_usd: 0, estimated_cost_usd: null, cost_partial: false }),
  'GET /api/questions': () => [],
  'GET /api/decisions': () => ({ decisions: [] }),
  'GET /api/chunk-counts': () => chunkCountsView(),
  'GET /api/runners': () => ({ runners: [] }),
  'GET /api/hosts': () => ({ hosts: [] }),
  'GET /api/projects': () => ({ projects: [] }),
  'GET /api/events': () => ({ events: [] }),
  'GET /api/activity': () => ({ activity: [] }),
};

/** Pump change detection until `ready()` holds, without `whenStable()`, which a dependent query never lets settle. */
async function pumpUntil(fixture: { detectChanges(): void }, ready: () => boolean, tries = 100): Promise<void> {
  for (let i = 0; i < tries; i += 1) {
    fixture.detectChanges();
    if (ready()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  fixture.detectChanges();
  if (!ready()) throw new Error('pumpUntil: the awaited content never rendered');
}

const box = (el: Element) => {
  const { x, y, width, height } = el.getBoundingClientRect();
  return { x, y, width, height };
};

describe('board dock shell sweep (web:shell-sweep)', () => {
  it('opens the dock beneath the board and restores the board on dismissal', async () => {
    const stub = stubHubClient(ROUTES);
    try {
      await TestBed.configureTestingModule({
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
          provideRouter([{ path: 'board', component: BoardPage }]),
        ],
      }).compileComponents();
      await page.viewport(1280, 800);
      const harness = await RouterTestingHarness.create();
      const root = harness.fixture.nativeElement as HTMLElement;
      // Stands in for `App`'s height-capped flex column, which gives the routed page a definite height.
      root.style.cssText = 'display: flex; flex-direction: column; height: 800px; min-height: 0; overflow: hidden;';
      document.body.appendChild(root);
      try {
        await harness.navigateByUrl('/board');
        await pumpUntil(harness.fixture, () => root.querySelector(`[data-chunk="${CHUNK_ID}"]`) !== null);
        const board = root.querySelector('app-board-shell.board')!;
        const dock = root.querySelector('app-chunk-detail.dock')!;
        const column = root.querySelector('.col-center')!;

        expect(dock.classList.contains('closed')).toBe(true);
        expect(dock.getClientRects().length, 'the closed dock still takes a box').toBe(0);
        const atRest = box(board);
        expect(atRest.height, 'the at-rest board does not run the column').toBeGreaterThan(box(column).height * 0.9);

        await harness.navigateByUrl(`/board?chunk=${CHUNK_ID}`);
        await pumpUntil(harness.fixture, () => !dock.classList.contains('closed') && dock.getClientRects().length > 0);
        const docked = box(board);
        expect({ x: docked.x, y: docked.y, width: docked.width }, 'opening the dock moved or narrowed the board').toEqual({
          x: atRest.x,
          y: atRest.y,
          width: atRest.width,
        });
        expect(docked.height, 'opening the dock did not shorten the board').toBeLessThan(atRest.height);
        expect(box(dock).y, 'the dock does not open beneath the board').toBeGreaterThanOrEqual(docked.y + docked.height);

        await pumpUntil(harness.fixture, () => dock.querySelectorAll('.d-sec').length === 3);
        const panel = dock.querySelector('fleet-kit-panel[data-testid="chunk-detail"]')!;
        const dockBox = box(dock);
        expect(box(panel), 'the open dock panel does not fill its share').toEqual(dockBox);
        const columns = [...dock.querySelectorAll<HTMLElement>('.d-sec')];
        for (const column of columns) {
          const filler = document.createElement('div');
          filler.style.height = `${dockBox.height * 3}px`;
          column.appendChild(filler);
        }
        for (const [i, column] of columns.entries()) {
          const colBox = box(column);
          expect(colBox.y + colBox.height, `column ${i} overruns the dock`).toBeLessThanOrEqual(dockBox.y + dockBox.height + 1);
          expect(column.scrollHeight, `column ${i} does not overflow into its own scroll`).toBeGreaterThan(column.clientHeight);
          column.scrollTop = 100;
          expect(column.scrollTop, `column ${i} does not scroll`).toBe(100);
          for (const [j, sibling] of columns.entries()) {
            if (j !== i) expect(sibling.scrollTop, `scrolling column ${i} moved column ${j}`).toBe(0);
          }
          column.scrollTop = 0;
        }
        expect(box(dock), 'filling the columns grew the dock').toEqual(dockBox);
        for (const column of columns) column.lastElementChild!.remove();

        await harness.navigateByUrl('/board');
        await pumpUntil(harness.fixture, () => dock.classList.contains('closed'));
        expect(dock.getClientRects().length, 'the dismissed dock still takes a box').toBe(0);
        expect(box(board), 'dismissing the dock left the board short of its at-rest box').toEqual(atRest);
      } finally {
        root.remove();
      }
    } finally {
      stub.restore();
    }
  });
});
