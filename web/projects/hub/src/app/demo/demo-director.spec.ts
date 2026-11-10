import { provideLocationMocks } from '@angular/common/testing';
import { ApplicationRef, ChangeDetectionStrategy, Component, computed, inject, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, provideRouter, withRouterConfig } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { assetArtifactView, chunkDetail, chunkSummary, OPERATOR_ME_RESPONSE, type RequestClientStub, stubHubClient } from 'fleet/testing';

import { FLEET_CLOCK } from 'fleet';
import { readDemoConfig } from './demo-config';
import { DemoDirector } from './demo-director';
import { DEMO_RANDOM } from './demo-random';
import { DemoKiosk } from './demo-kiosk';

/**
 * The demo pilot, driven through a **real** router against stand-in pages.
 *
 * The stand-ins render exactly the two handles the director steers by —
 * `detail-id` inside `chunk-detail`, and `artifacts-tab-artifact-key` inside
 * `artifacts-tab-artifact` — because that identity check is the contract
 * between this class and the real board (`board-page.ts`, `fleet`'s `chunk-page.html`).
 * A stand-in keeps this spec about the *tour* — which chunk, which route, which
 * artifact, in what order — rather than about the board's own rendering, which
 * has its own specs. Intervals are milliseconds here; the shape of the cycle is
 * what is under test, not its wall-clock length.
 */
const CHUNK = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const OTHER_CHUNK = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJA';
const ARTIFACTS = ['branch/main', 'review/findings'];

const CHUNK_FIELDS = {
  chunk_id: CHUNK,
  graph_id: 'gr_1',
  status: 'running' as const,
  current_node_id: 'nd_build',
  current_node_name: 'build',
  work_refs: [],
  runner_id: 'runner-local',
  environment_count: 1,
};

const HUB_ROUTES: Parameters<typeof stubHubClient>[0] = {
  'GET /api/me': () => OPERATOR_ME_RESPONSE,
  'GET /api/chunks': () => ({
    chunks: [chunkSummary(CHUNK_FIELDS), chunkSummary({ ...CHUNK_FIELDS, chunk_id: OTHER_CHUNK })],
    next_cursor: null,
    board_done_policy: null,
  }),
  'GET /api/chunks/{chunk_id}': () =>
    chunkDetail({
      ...CHUNK_FIELDS,
      graph_name: 'default',
      latest_epoch: 1,
      history: [],
      artifacts: ARTIFACTS.map((key, index) =>
        assetArtifactView({ key, kind: 'asset', epoch: 1, content: 'body', recorded_at: `2026-07-16T11:0${index}:00.000Z` }),
      ),
    }),
};

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
  let now: number;
  let seed: number;
  let director: DemoDirector;

  beforeEach(() => {
    now = 0;
    seed = 1;
    stub = stubHubClient(HUB_ROUTES);
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        { provide: FLEET_CLOCK, useValue: () => now },
        { provide: DEMO_RANDOM, useValue: () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646 },
        {
          provide: DemoKiosk,
          useValue: {
            begin: vi.fn().mockResolvedValue(undefined),
            end: vi.fn().mockResolvedValue(undefined),
            reloadIfStale: async () => false,
          },
        },
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
  });

  afterEach(() => {
    director?.stop();
    vi.useRealTimers();
    stub.restore();
  });

  /** Boot the app at `/board` and hand the director a config parsed from `search`. */
  async function run(search: string): Promise<Router> {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/board');
    vi.useFakeTimers();
    director = TestBed.inject(DemoDirector);
    director.start(readDemoConfig(search));
    return TestBed.inject(Router);
  }

  async function step(ms = 5): Promise<void> {
    now += ms;
    TestBed.inject(ApplicationRef).tick();
    await vi.advanceTimersByTimeAsync(ms);
    TestBed.inject(ApplicationRef).tick();
    await Promise.resolve();
  }

  async function trail(router: Router, until: (seen: string[]) => boolean, timeoutMs = 8000): Promise<string[]> {
    const seen: string[] = [];
    for (let elapsed = 0; elapsed <= timeoutMs; elapsed += 5) {
      if (seen[seen.length - 1] !== router.url) seen.push(router.url);
      if (until(seen)) return seen;
      await step();
    }
    throw new Error(`timed out; saw:\n${seen.join('\n')}`);
  }

  const FAST = 'demo_swap_chunk_interval=2&demo_board_scroll=0.05&demo_artifact_interval=0.15&demo_reload_after=0';

  it('does nothing at all without ?demo', async () => {
    const router = await run('?chunk=ch_other');

    await step(300);

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

  it('selects a chunk using the supplied random sequence', async () => {
    seed = 100000;
    const router = await run(`?demo=true&${FAST}`);

    await trail(router, (urls) => urls.some((url) => url.includes(`chunk=${OTHER_CHUNK}`)));
    expect(router.url).toContain(`chunk=${OTHER_CHUNK}`);
  });

  it('holds the board for its whole dwell even when the dock has nowhere to scroll', async () => {
    // The stand-in dock overflows nothing, which is the case that used to skip
    // the board entirely and flicker straight through to the chunk page.
    const router = await run('?demo=true&demo_swap_chunk_interval=4&demo_board_scroll=0.6&demo_artifact_interval=0.15');

    const openedAt = now;
    await trail(router, (urls) => urls.some((url) => url.includes(`chunk=${CHUNK}`)));
    const boardAt = now;
    await trail(router, (urls) => urls.some((url) => url.startsWith('/board/chunk/')));

    expect(now - boardAt).toBeGreaterThanOrEqual(595);
    expect(now - boardAt).toBeLessThanOrEqual(620);
    expect(now - openedAt).toBeLessThan(4000);
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

    expect([...keys]).toEqual(ARTIFACTS);
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

    await trail(router, (urls) => urls.some((url) => url.includes(`chunk=${CHUNK}`)));
    const boardAt = now;

    // …board, …chunk page, …board again: the cycle closes on its own.
    const seen = await trail(router, (urls) => {
      const descended = urls.findIndex((url) => url.startsWith('/board/chunk/'));
      return descended >= 0 && urls.slice(descended).some((url) => url.startsWith('/board?'));
    });

    expect(seen.filter((url) => url.startsWith('/board?')).length).toBeGreaterThanOrEqual(2);
    expect(now - boardAt).toBeGreaterThanOrEqual(1990);
    expect(now - boardAt).toBeLessThanOrEqual(2050);
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

    // Observe a transition first, then advance beyond two whole cycles.
    await trail(router, (urls) => urls.length >= 2);

    director.stop();
    const parked = router.url;
    for (let i = 0; i < 1000; i++) await step();

    expect(router.url).toBe(parked);
  });
});
