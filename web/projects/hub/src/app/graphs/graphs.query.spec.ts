import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { graphView, settle, type RequestClientStub, stubError, stubRequestClient } from 'fleet/testing';
import { hubClient } from 'fleet';
import { GraphFetchError, injectHubGraphNamesQuery, injectHubGraphQuery, shouldRetryGraphFetch } from './graphs.query';

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
    stub = stubRequestClient(hubClient, () => stubError(404, { detail: 'unknown graph' }));
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
  selector: 'app-test-graph-names-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestGraphNamesHost {
  readonly graphIds = signal<readonly string[]>(['gr_1', 'gr_2', 'gr_1', 'gr_gone']);
  readonly names = injectHubGraphNamesQuery(() => this.graphIds());
  readonly graph = injectHubGraphQuery(() => 'gr_1');
}

describe('injectHubGraphNamesQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('names each readable graph by its id, once per id, sharing the single-graph read\'s cache entry', async () => {
    stub = stubRequestClient(hubClient, (_method, path) => {
      if (path === '/api/graphs/gr_1') return graphView({ graph_id: 'gr_1', name: 'adv-dwf' });
      if (path === '/api/graphs/gr_2') return graphView({ graph_id: 'gr_2', name: 'bas-dwf' });
      return stubError(404, { detail: 'unknown graph' });
    });
    TestBed.configureTestingModule({
      imports: [TestGraphNamesHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestGraphNamesHost);
    await settle(fixture);

    expect([...fixture.componentInstance.names()]).toEqual([
      ['gr_1', 'adv-dwf'],
      ['gr_2', 'bas-dwf'],
    ]);
    expect(stub.forRoute('/api/graphs/gr_1', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/graphs/gr_gone', 'GET')).toHaveLength(1);
  });
});
