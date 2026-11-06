import { provideLocationMocks } from '@angular/common/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import {
  chunkCountsView,
  chunkDetail,
  chunkSummary,
  fleetSpendView,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  settle,
  stubHubClient,
  workItemEntry,
  workRefView,
} from 'fleet/testing';
import { BoardPage } from './board-page';

const RUNNING = 'ch_01KXKVVF1J3D6H6VYZ3XYNZPRA';

const CHUNK = chunkSummary({
  chunk_id: RUNNING,
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  work_refs: [workRefView({ source: 'hub', ref: '1', label: 'hub#1', web_url: null })],
  graph_name: 'default',
  graph_progress: [],
});

/** The board's reads, each answered empty but for one running chunk and its one titled work item. */
const ROUTES: Parameters<typeof stubHubClient>[0] = {
  'GET /api/me': () => OPERATOR_ME_RESPONSE,
  'GET /api/chunks': () => ({ chunks: [CHUNK], next_cursor: null }),
  'GET /api/chunks/{chunk_id}': () =>
    chunkDetail({
      ...CHUNK,
      graph_name: 'default',
      latest_epoch: 1,
      history: [],
      artifacts: [],
      open_prs: [],
      closed_prs: [],
      landed_repos: [],
    }),
  'GET /api/chunks/{chunk_id}/work-items': () => ({ items: [] }),
  'GET /api/work-items': () => ({
    chunks: { [RUNNING]: [workItemEntry({ source: 'hub', ref: '1', label: 'hub#1', title: 'Fix the lease', comments: [] })] },
  }),
  'GET /api/queue': () => ({ entries: [], next_cursor: null }),
  'GET /api/backlog': () => ({ entries: [], next_cursor: null }),
  'GET /api/questions': () => [],
  'GET /api/spend': () => fleetSpendView(),
  'GET /api/decisions': () => ({ decisions: [] }),
  'GET /api/chunk-counts': () => chunkCountsView(),
  'GET /api/runners': () => ({ runners: [] }),
  'GET /api/hosts': () => ({ hosts: [] }),
  'GET /api/projects': () => ({ projects: [] }),
  'GET /api/events': () => ({ events: [] }),
  'GET /api/activity': () => ({ activity: [] }),
};

describe('BoardPage layout and titles', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubHubClient(ROUTES);
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'board', component: BoardPage }]),
        provideLocationMocks(),
      ],
    });
  });

  afterEach(() => stub.restore());

  async function open(url: string): Promise<{ el: HTMLElement; harness: RouterTestingHarness }> {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { el: harness.fixture.nativeElement as HTMLElement, harness };
  }

  it("reads the chunks' work items after the chunk list, and leads each card with its titles", async () => {
    const { el } = await open('/board');

    const paths = stub.requests.map((request) => request.path);
    expect(paths.indexOf('/api/work-items')).toBeGreaterThan(paths.indexOf('/api/chunks'));
    const titled = el.querySelector<HTMLElement>(`[data-chunk="${RUNNING}"] [data-testid="chunk-title"]`);
    expect(titled?.textContent?.trim()).toBe('Fix the lease');
  });

  it('switches to the list layout through the URL, keeping the open chunk', async () => {
    const { el, harness } = await open(`/board?chunk=${RUNNING}`);

    el.querySelector<HTMLElement>('[data-testid="board-layout-list"]')!.click();
    await settle(harness.fixture);

    const router = TestBed.inject(Router);
    expect(router.parseUrl(router.url).queryParams).toEqual({ chunk: RUNNING, layout: 'list' });
    expect(el.querySelector('[data-testid="board-list"]')).not.toBeNull();
  });

  it('opens straight into the list layout from the URL', async () => {
    const { el } = await open('/board?layout=list');

    expect(el.querySelector('[data-testid="board-list"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="board"]')).toBeNull();
  });
});
