import {
  ChangeDetectionStrategy,
  Component,
  provideZonelessChangeDetection,
} from '@angular/core';
import { TestBed } from '@angular/core/testing';
import {
  QueryClient,
  provideTanStackQuery,
} from '@tanstack/angular-query-experimental';

import type { ChunkSummary, hubApi } from 'fleet';
import {
  chunkSummary,
  settle,
  type RequestClientStub,
  stubError,
  stubHubClient,
} from 'fleet/testing';
import { boardChunksQueryOptions, injectHubBoardChunksQuery, runnerChunksQueryOptions } from './chunks.query';
import type { BoardChunksRead } from './chunks.model';

@Component({
  selector: 'app-test-chunks-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestChunksQueryHost {
  readonly query = injectHubBoardChunksQuery();
}

function chunk(id: string): ChunkSummary {
  return chunkSummary({ chunk_id: id, graph_id: 'gr_1', status: 'ready' });
}

function mount() {
  TestBed.configureTestingModule({
    imports: [TestChunksQueryHost],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(
        new QueryClient({ defaultOptions: { queries: { retry: false } } }),
      ),
    ],
  });
  return TestBed.createComponent(TestChunksQueryHost);
}

describe('injectHubBoardChunksQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads a single-page GET /api/chunks whole (blizzard#526) — one request when next_cursor is already null', async () => {
    stub = stubHubClient({
      'GET /api/chunks': () => ({ chunks: [chunk('ch_1')], next_cursor: null, board_done_policy: null }),
    });
    const fixture = mount();
    await settle(fixture);

    expect(
      fixture.componentInstance.query.data()?.chunks.map((c) => c.chunk_id),
    ).toEqual(['ch_1']);
    expect(stub.forRoute('/api/chunks', 'GET')).toHaveLength(1);
  });

  it('projects statuses for observers without changing cached wire data', async () => {
    stub = stubHubClient({
      'GET /api/chunks': () => ({ chunks: [chunk('ch_1')], next_cursor: null, board_done_policy: null }),
    });
    const fixture = mount();
    await settle(fixture);
    expect(fixture.componentInstance.query.data()?.chunks[0].laneStatus).toEqual({ kind: 'ready' });
    const client = TestBed.inject(QueryClient);
    const cached = client.getQueryData<BoardChunksRead>(boardChunksQueryOptions(null).queryKey);
    expect(cached?.chunks[0]).not.toHaveProperty('laneStatus');
  });

  it('preserves unknown statuses and server judgments in the observer and raw cache', async () => {
    const future = {
      ...chunk('ch_future'),
      status: 'future_status' as ChunkSummary['status'],
      terminal: true,
      promotable: false,
      graph_progress: [{ node_id: 'nd_1', name: 'build', state: 'future' as hubApi.GraphProgressState }],
    };
    stub = stubHubClient({
      'GET /api/chunks': () => ({ chunks: [future], next_cursor: null, board_done_policy: null }),
    });
    const fixture = mount();
    await settle(fixture);

    const observed = fixture.componentInstance.query.data()?.chunks[0];
    expect(observed?.laneStatus).toEqual({ kind: 'unknown', raw: 'future_status' });
    expect(observed?.terminal).toBe(true);
    expect(observed?.promotable).toBe(false);
    expect(observed?.graphProgress).toEqual([{
      nodeId: 'nd_1',
      name: 'build',
      state: { kind: 'unknown', raw: 'future' },
    }]);
    const cached = TestBed.inject(QueryClient).getQueryData<BoardChunksRead>(boardChunksQueryOptions(null).queryKey);
    expect(cached?.chunks[0]).toEqual(future);
    expect(cached?.pages[0].chunks[0]).toEqual(future);
  });

  it('projects runner claims from the same tenant-wide raw cache', async () => {
    const held = { ...chunk('ch_held'), runner_id: 'rn_1', workspace_id: 'ws_1', environment_count: 2 };
    stub = stubHubClient({
      'GET /api/chunks': () => ({ chunks: [held], next_cursor: null, board_done_policy: null }),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const runner = runnerChunksQueryOptions();
    const board = boardChunksQueryOptions(null);
    const raw = await client.fetchQuery(board);
    expect(runner.queryKey).toEqual(board.queryKey);
    expect(runner.select(raw).chunks[0]).toEqual({
      chunk_id: 'ch_held', runner_id: 'rn_1', workspace_id: 'ws_1', environment_count: 2,
      current_node_id: null, current_node_name: null, project_id: null, project_slug: null,
      laneStatus: { kind: 'ready' },
    });
    expect(client.getQueryData(board.queryKey)).toEqual(raw);
    expect(stub.forRoute('/api/chunks', 'GET')).toHaveLength(1);
  });

  it('drains every page of a multi-page response and concatenates them in order', async () => {
    let calls = 0;
    stub = stubHubClient({
      'GET /api/chunks': () => {
        calls += 1;
        return calls === 1
          ? { chunks: [chunk('ch_1'), chunk('ch_2')], next_cursor: 'cursor-1', board_done_policy: null }
          : { chunks: [chunk('ch_3')], next_cursor: null, board_done_policy: null };
      },
    });
    const fixture = mount();
    await settle(fixture);

    expect(
      fixture.componentInstance.query.data()?.chunks.map((c) => c.chunk_id),
    ).toEqual(['ch_1', 'ch_2', 'ch_3']);
    expect(fixture.componentInstance.query.data()?.pages.map((p) => p.next_cursor)).toEqual(['cursor-1', null]);
    expect(fixture.componentInstance.query.data()?.pages[0]).not.toHaveProperty('chunks');
    const cached = TestBed.inject(QueryClient).getQueryData<BoardChunksRead>(boardChunksQueryOptions(null).queryKey);
    expect(cached?.pages.map((page) => page.chunks.length)).toEqual([2, 1]);
    expect(stub.forRoute('/api/chunks', 'GET')).toHaveLength(2);
  });

  it('surfaces a mid-drain error as the query error, not a partial result', async () => {
    let calls = 0;
    stub = stubHubClient({
      'GET /api/chunks': () => {
        calls += 1;
        return calls === 1
          ? { chunks: [chunk('ch_1')], next_cursor: 'cursor-1', board_done_policy: null }
          : stubError(503, { detail: 'unavailable' });
      },
    });
    const fixture = mount();
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
    expect(fixture.componentInstance.query.data()).toBeUndefined();
  });

  it('keeps the previous rows and pages in the cache when a later page fails on refresh', async () => {
    let refreshing = false;
    const oldPolicy = { window_seconds: 7200, floor: 3, done_since: '2026-11-01T00:00:00Z' };
    const newPolicy = { ...oldPolicy, done_since: '2026-11-02T00:00:00Z' };
    stub = stubHubClient({
      'GET /api/chunks': (request) => {
        const cursor = new URLSearchParams(request.search).get('cursor');
        if (cursor && refreshing) return stubError(503, { detail: 'unavailable' });
        return cursor
          ? { chunks: [chunk('old-2')], next_cursor: null, board_done_policy: oldPolicy }
          : { chunks: [chunk(refreshing ? 'new-1' : 'old-1')], next_cursor: 'next', board_done_policy: refreshing ? newPolicy : oldPolicy };
      },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const options = boardChunksQueryOptions(null);
    const previous = await client.fetchQuery(options);
    refreshing = true;

    await expect(client.fetchQuery(options)).rejects.toBeDefined();
    expect(client.getQueryData(options.queryKey)).toEqual(previous);
    expect(previous.chunks.map((c) => c.chunk_id)).toEqual(['old-1', 'old-2']);
    expect(previous.pages.map((page) => page.next_cursor)).toEqual(['next', null]);
    expect(client.getQueryData<BoardChunksRead>(options.queryKey)?.pages.map((page) => page.board_done_policy)).toEqual([oldPolicy, oldPolicy]);
    expect(stub.forRoute('/api/chunks', 'GET')).toHaveLength(4);
  });

  it('caches each project lens separately from the tenant and other projects', async () => {
    stub = stubHubClient({
      'GET /api/chunks': (request) => {
        const project = new URLSearchParams(request.search).get('project');
        return { chunks: [chunk(project ?? 'tenant')], next_cursor: null, board_done_policy: null };
      },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const tenant = boardChunksQueryOptions(null);
    const alpha = boardChunksQueryOptions('alpha');
    const beta = boardChunksQueryOptions('beta');
    await Promise.all([client.fetchQuery(tenant), client.fetchQuery(alpha), client.fetchQuery(beta)]);

    expect(client.getQueryData<BoardChunksRead>(tenant.queryKey)?.chunks.map((c) => c.chunk_id)).toEqual(['tenant']);
    expect(client.getQueryData<BoardChunksRead>(alpha.queryKey)?.chunks.map((c) => c.chunk_id)).toEqual(['alpha']);
    expect(client.getQueryData<BoardChunksRead>(beta.queryKey)?.chunks.map((c) => c.chunk_id)).toEqual(['beta']);
  });

  it('continues past an empty intermediate page and preserves it in the cached pages', async () => {
    stub = stubHubClient({
      'GET /api/chunks': (request) => {
        const cursor = new URLSearchParams(request.search).get('cursor');
        if (cursor === 'first') return { chunks: [], next_cursor: 'second', board_done_policy: null };
        if (cursor === 'second') return { chunks: [chunk('last')], next_cursor: null, board_done_policy: null };
        return { chunks: [chunk('first')], next_cursor: 'first', board_done_policy: null };
      },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const options = boardChunksQueryOptions(null);
    await client.fetchQuery(options);

    const cached = client.getQueryData<Awaited<ReturnType<typeof options.queryFn>>>(options.queryKey);
    expect(cached?.chunks.map((c) => c.chunk_id)).toEqual(['first', 'last']);
    expect(cached?.pages.map((page) => page.chunks.length)).toEqual([1, 0, 1]);
    expect(cached?.pages.map((page) => page.next_cursor)).toEqual(['first', 'second', null]);
  });
});
