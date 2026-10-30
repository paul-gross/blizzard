import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, stubRequestClient } from 'fleet/testing';
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
 * Proven able to fail by dropping `.dock.closed`'s `display: none`: the closed dock then
 * takes room, and the at-rest board no longer runs the column's full height.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */

const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYNDOCK';
const CHUNK = { chunk_id: CHUNK_ID, graph_id: 'gr_1', status: 'running', current_node_id: 'nd_build', work_refs: [] };

function answer(_method: string, path: string): unknown {
  switch (path) {
    case '/api/me':
      return OPERATOR_ME_RESPONSE;
    case '/api/chunks':
      return { chunks: [CHUNK], next_cursor: null };
    case `/api/chunks/${CHUNK_ID}`:
      return { ...CHUNK, graph_name: 'default', latest_epoch: 1, history: [], artifacts: [] };
    case `/api/chunks/${CHUNK_ID}/work-items`:
      return { items: [] };
    case '/api/queue':
    case '/api/backlog':
      return { entries: [], next_cursor: null };
    case '/api/questions':
      return [];
    case '/api/decisions':
      return { decisions: [] };
    default:
      return {};
  }
}

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
    const stub = stubRequestClient(hubClient, answer);
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
