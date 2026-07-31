import { provideLocationMocks } from '@angular/common/testing';
import { ApplicationRef, ChangeDetectionStrategy, Component, computed, inject, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, provideRouter, withRouterConfig } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, stubRequestClient } from 'fleet/testing';

import { readDemoConfig } from './demo-config';
import { DemoDirector } from './demo-director';

/**
 * The demo pilot, driven through a **real** router against stand-in pages.
 *
 * The stand-ins render exactly the two handles the director steers by —
 * `detail-id` inside `chunk-detail`, and `artifacts-tab-artifact-key` inside
 * `artifacts-tab-artifact` — because that identity check is the contract
 * between this class and the real board (`board-page.ts`, `chunk-artifacts-tab.ts`).
 * A stand-in keeps this spec about the *tour* — which chunk, which route, which
 * artifact, in what order — rather than about the board's own rendering, which
 * has its own specs. Intervals are milliseconds here; the shape of the cycle is
 * what is under test, not its wall-clock length.
 */
const CHUNK = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const ARTIFACTS = ['branch/main', 'review/findings'];

const CHUNK_ROW = {
  chunk_id: CHUNK,
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  current_node_name: 'build',
  model: 'claude-opus-5',
  work_refs: [],
  runner_id: 'runner-local',
  environment_count: 1,
};

function hubRoutes(method: string, path: string): unknown {
  if (method !== 'GET') return {};
  if (path === '/api/me') return OPERATOR_ME_RESPONSE;
  if (path === '/api/chunks') return [CHUNK_ROW];
  if (/^\/api\/chunks\/[^/]+$/.test(path)) {
    return {
      ...CHUNK_ROW,
      graph_name: 'default',
      latest_epoch: 1,
      history: [],
      artifacts: ARTIFACTS.map((key, index) => ({
        key,
        kind: 'asset',
        epoch: 1,
        content: 'body',
        recorded_at: `2026-07-16T11:0${index}:00.000Z`,
      })),
    };
  }
  return {};
}

/** The board stand-in: the dock, labelled with whatever `?chunk` names. */
@Component({
  selector: 'app-demo-board-stub',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (chunk(); as id) {
      <div data-testid="chunk-detail"><span data-testid="detail-id">{{ id }}</span></div>
    }
  `,
})
class BoardStub {
  private readonly params = toSignal(inject(ActivatedRoute).queryParamMap, {
    initialValue: inject(ActivatedRoute).snapshot.queryParamMap,
  });
  protected readonly chunk = computed(() => this.params().get('chunk'));
}

/** The chunk page stand-in: the artifact viewer, labelled with `?artifact`. */
@Component({
  selector: 'app-demo-chunk-stub',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (artifact(); as key) {
      <div data-testid="artifacts-tab-artifact">
        <span data-testid="artifacts-tab-artifact-key">{{ key }}</span>
      </div>
    }
  `,
})
class ChunkStub {
  private readonly params = toSignal(inject(ActivatedRoute).queryParamMap, {
    initialValue: inject(ActivatedRoute).snapshot.queryParamMap,
  });
  protected readonly artifact = computed(() => this.params().get('artifact'));
}

describe('DemoDirector', () => {
  let stub: RequestClientStub;
  let pump: ReturnType<typeof setInterval>;
  let director: DemoDirector;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, hubRoutes);
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(
          [
            { path: 'board', component: BoardStub },
            { path: 'board/chunk/:chunkId', component: ChunkStub },
          ],
          withRouterConfig({ onSameUrlNavigation: 'reload' }),
        ),
        provideLocationMocks(),
      ],
    });
    // The director waits on rendered DOM, so something has to keep the app
    // ticking while its loop runs — the fixture's own change detection is not
    // pumped by a spec that never awaits it.
    pump = setInterval(() => {
      try {
        TestBed.inject(ApplicationRef).tick();
      } catch {
        // Torn down between ticks.
      }
    }, 5);
  });

  afterEach(() => {
    clearInterval(pump);
    director?.stop();
    stub.restore();
  });

  /** Boot the app at `/board` and hand the director a config parsed from `search`. */
  async function run(search: string): Promise<Router> {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/board');
    director = TestBed.inject(DemoDirector);
    director.start(readDemoConfig(search));
    return TestBed.inject(Router);
  }

  /** Poll the router's URL until `matches` accepts one, collecting every distinct
   * URL seen — the tour's trail. */
  async function trail(router: Router, until: (seen: string[]) => boolean, timeoutMs = 8000): Promise<string[]> {
    const seen: string[] = [];
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      if (seen[seen.length - 1] !== router.url) seen.push(router.url);
      if (until(seen)) return seen;
      if (Date.now() > deadline) throw new Error(`timed out; saw:\n${seen.join('\n')}`);
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  }

  const FAST = 'demo_swap_chunk_interval=2&demo_board_scroll=0.05&demo_artifact_interval=0.15&demo_reload_after=0';

  it('does nothing at all without ?demo', async () => {
    const router = await run('?chunk=ch_other');

    await new Promise((resolve) => setTimeout(resolve, 300));

    expect(router.url).toBe('/board');
  });

  it('opens a chunk on the board, then descends into its artifacts', async () => {
    const router = await run(`?demo=true&${FAST}`);

    const seen = await trail(router, (urls) => urls.some((url) => url.startsWith('/board/chunk/')));

    expect(seen.some((url) => url.startsWith('/board?') && url.includes(`chunk=${CHUNK}`))).toBe(true);
    const artifactUrl = seen.find((url) => url.startsWith('/board/chunk/'));
    expect(artifactUrl).toContain(`/board/chunk/${CHUNK}`);
    expect(artifactUrl).toContain('tab=artifacts');
  });

  it('holds the board for its whole dwell even when the dock has nowhere to scroll', async () => {
    // The stand-in dock overflows nothing, which is the case that used to skip
    // the board entirely and flicker straight through to the chunk page.
    const router = await run('?demo=true&demo_swap_chunk_interval=4&demo_board_scroll=0.6&demo_artifact_interval=0.15');

    const openedAt = Date.now();
    await trail(router, (urls) => urls.some((url) => url.includes(`chunk=${CHUNK}`)));
    const boardAt = Date.now();
    await trail(router, (urls) => urls.some((url) => url.startsWith('/board/chunk/')));

    expect(Date.now() - boardAt).toBeGreaterThanOrEqual(400);
    expect(Date.now() - openedAt).toBeLessThan(4000);
  });

  it('rotates through the chunk store, a different artifact each dwell', async () => {
    const router = await run(`?demo=true&${FAST}`);

    const keys = new Set<string>();
    await trail(router, (urls) => {
      for (const url of urls) {
        const match = /[?&]artifact=([^&]+)/.exec(url);
        if (match !== null) keys.add(decodeURIComponent(match[1]));
      }
      return keys.size >= ARTIFACTS.length;
    });

    expect([...keys].sort()).toEqual([...ARTIFACTS].sort());
  });

  it('carries the demo params on every navigation, so a kiosk reload resumes the tour', async () => {
    const router = await run(`?demo=true&${FAST}`);

    const seen = await trail(router, (urls) => urls.filter((url) => url !== '/board').length >= 2);

    for (const url of seen.filter((candidate) => candidate !== '/board')) {
      expect(url).toContain('demo=true');
      expect(url).toContain('demo_swap_chunk_interval=2');
    }
  });

  it('returns to the board when the swap interval is up', async () => {
    const router = await run(`?demo=true&${FAST}`);

    // …board, …chunk page, …board again: the cycle closes on its own.
    const seen = await trail(router, (urls) => {
      const descended = urls.findIndex((url) => url.startsWith('/board/chunk/'));
      return descended >= 0 && urls.slice(descended).some((url) => url.startsWith('/board?'));
    });

    expect(seen.filter((url) => url.startsWith('/board?')).length).toBeGreaterThanOrEqual(2);
  });

  it('is idempotent — a second start does not put a second tour on the screen', async () => {
    const router = await run(`?demo=true&${FAST}`);
    director.start(readDemoConfig(`?demo=true&${FAST}`));

    await trail(router, (urls) => urls.some((url) => url.startsWith('/board/chunk/')));

    // One navigation per step: a doubled loop would show the same URL twice in a row,
    // which `trail` collapses — so assert on the reads instead, which do not collapse.
    expect(stub.forRoute('/api/chunks').length).toBeGreaterThan(0);
    expect(router.url.startsWith('/board/chunk/')).toBe(true);
  });

  it('stops on demand', async () => {
    const router = await run(`?demo=true&${FAST}`);
    await trail(router, (urls) => urls.some((url) => url.startsWith('/board/chunk/')));

    director.stop();
    const parked = router.url;
    await new Promise((resolve) => setTimeout(resolve, 400));

    expect(router.url).toBe(parked);
  });
});
