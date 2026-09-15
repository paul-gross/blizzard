import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { injectHubChunksQuery } from './chunks.query';

@Component({
  selector: 'fleet-test-chunks-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestChunksQueryHost {
  readonly query = injectHubChunksQuery();
}

function chunk(id: string): unknown {
  return { chunk_id: id, graph_id: 'gr_1', status: 'ready', current_node_id: null };
}

function mount() {
  TestBed.configureTestingModule({
    imports: [TestChunksQueryHost],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  });
  return TestBed.createComponent(TestChunksQueryHost);
}

describe('injectHubChunksQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads a single-page GET /api/chunks whole (blizzard#526) — one request when next_cursor is already null', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/chunks') return { chunks: [chunk('ch_1')], next_cursor: null };
      return {};
    });
    const fixture = mount();
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.map((c) => c.chunk_id)).toEqual(['ch_1']);
    expect(stub.forRoute('/api/chunks', 'GET')).toHaveLength(1);
  });

  it('drains every page of a multi-page response and concatenates them in order', async () => {
    let calls = 0;
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/chunks') {
        calls += 1;
        return calls === 1
          ? { chunks: [chunk('ch_1'), chunk('ch_2')], next_cursor: 'cursor-1' }
          : { chunks: [chunk('ch_3')], next_cursor: null };
      }
      return {};
    });
    const fixture = mount();
    await settle(fixture);

    expect(fixture.componentInstance.query.data()?.map((c) => c.chunk_id)).toEqual(['ch_1', 'ch_2', 'ch_3']);
    expect(stub.forRoute('/api/chunks', 'GET')).toHaveLength(2);
  });

  it('surfaces a mid-drain error as the query error, not a partial result', async () => {
    let calls = 0;
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/chunks') {
        calls += 1;
        return calls === 1
          ? { chunks: [chunk('ch_1')], next_cursor: 'cursor-1' }
          : stubError(503, { detail: 'unavailable' });
      }
      return {};
    });
    const fixture = mount();
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
    expect(fixture.componentInstance.query.data()).toBeUndefined();
  });
});
