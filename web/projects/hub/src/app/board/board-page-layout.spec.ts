import { provideLocationMocks } from '@angular/common/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { BoardPage } from './board-page';

const RUNNING = 'ch_01KXKVVF1J3D6H6VYZ3XYNZPRA';

const CHUNK = {
  chunk_id: RUNNING,
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  work_refs: [{ source: 'hub', ref: '1', label: 'hub#1' }],
};

/** The board's reads, each answered empty but for one running chunk and its one titled work item;
 * everything unnamed falls through to `{}`, which the envelope reads unwrap to an idle fleet. */
function answer(_method: string, path: string): unknown {
  switch (path) {
    case '/api/me':
      return OPERATOR_ME_RESPONSE;
    case '/api/chunks':
      return { chunks: [CHUNK], next_cursor: null };
    case `/api/chunks/${RUNNING}`:
      return { ...CHUNK, graph_name: 'default', latest_epoch: 1, history: [], artifacts: [], open_prs: [], closed_prs: [], landed_repos: [] };
    case '/api/work-items':
      return { chunks: { [RUNNING]: [{ source: 'hub', ref: '1', title: 'Fix the lease', fetched_at: 't', comments: [] }] } };
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

describe('BoardPage layout and titles', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubRequestClient(hubClient, answer);
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
