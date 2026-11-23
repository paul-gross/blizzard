import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { graphSummaryView, graphView, settle, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { GraphFetchError, injectHubGraphQuery, injectHubGraphsQuery, shouldRetryGraphFetch } from './graphs.query';

describe('shouldRetryGraphFetch', () => {
  it('never retries a 404 — the graph id is unknown, not a transient failure', () => {
    expect(shouldRetryGraphFetch(0, new GraphFetchError(404))).toBe(false);
    expect(shouldRetryGraphFetch(1, new GraphFetchError(404))).toBe(false);
  });

  it('retries a non-404 GraphFetchError up to the default cap of 3 attempts', () => {
    expect(shouldRetryGraphFetch(0, new GraphFetchError(500))).toBe(true);
    expect(shouldRetryGraphFetch(2, new GraphFetchError(500))).toBe(true);
    expect(shouldRetryGraphFetch(3, new GraphFetchError(500))).toBe(false);
  });

  it('retries any other error (e.g. a network failure) up to the default cap', () => {
    expect(shouldRetryGraphFetch(0, new Error('network down'))).toBe(true);
    expect(shouldRetryGraphFetch(3, new Error('network down'))).toBe(false);
  });
});

@Component({
  selector: 'app-test-graph-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestGraphQueryHost {
  readonly graphId = signal<string | null>('gr_missing');
  readonly query = injectHubGraphQuery(() => this.graphId());
}

describe('injectHubGraphQuery (404 wiring)', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('issues exactly one request for an unknown graph id — no retries against the real query client', async () => {
    stub = stubHubClient({ 'GET /api/graphs/{graph_id}': () => stubError(404, { detail: 'unknown graph' }) });
    TestBed.configureTestingModule({
      imports: [TestGraphQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestGraphQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
    expect(stub.forRoute('/api/graphs/gr_missing', 'GET')).toHaveLength(1);
  });
});

@Component({
  selector: 'app-test-graph-reads-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestGraphReadsHost {
  readonly list = injectHubGraphsQuery();
  readonly graph = injectHubGraphQuery(() => 'gr_build');
}

describe('graph reads (domain projection)', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function mount(routes: Parameters<typeof stubHubClient>[0]) {
    stub = stubHubClient(routes);
    TestBed.configureTestingModule({
      imports: [TestGraphReadsHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))],
    });
    const fixture = TestBed.createComponent(TestGraphReadsHost);
    await settle(fixture);
    return fixture.componentInstance;
  }

  it('serves the graph list as summaries, in the order the hub returned them', async () => {
    const host = await mount({
      'GET /api/graphs': () => [
        graphSummaryView({ graph_id: 'gr_b', name: 'b', lifecycle: 'archived' as never }),
        graphSummaryView({ graph_id: 'gr_a', name: 'a' }),
      ],
      'GET /api/graphs/{graph_id}': () => graphView(),
    });

    expect(host.list.data()?.map((g) => g.graphId)).toEqual(['gr_b', 'gr_a']);
    expect(host.list.data()?.[0].lifecycle).toEqual({ kind: 'unknown', raw: 'archived' });
  });

  it('serves one graph as the domain graph', async () => {
    const host = await mount({
      'GET /api/graphs': () => [],
      'GET /api/graphs/{graph_id}': () => graphView({ graph_id: 'gr_build', entry_node_id: 'nd_build' }),
    });

    expect(host.graph.data()?.graphId).toBe('gr_build');
    expect(host.graph.data()?.entryNodeId).toBe('nd_build');
    expect(host.list.data()).toEqual([]);
  });

  it('surfaces a failed graph-list read as an error', async () => {
    const host = await mount({
      'GET /api/graphs': () => stubError(500, { detail: 'boom' }),
      'GET /api/graphs/{graph_id}': () => graphView(),
    });

    expect(host.list.isError()).toBe(true);
  });
});
