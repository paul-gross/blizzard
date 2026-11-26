import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';
import { settle } from 'fleet/testing';
import { HUB_PROJECT_FILTER } from '../../core/project-filter';
import { hubChunkCountsKey, hubChunksKey, lensedKey } from '../../core/query-keys';
import { injectHubChunkCountsQuery } from './chunk-counts.query';

const ALL = { not_ready: 1, ready: 2, running: 3, delivering: 4, waiting_on_human: 5, needs_human: 6, paused: 7, stopped: 8, done: 9, terminal: 20, total: 99, done_since: 123 };
const PROJECT = { ...ALL, ready: 12, total: 42 };

@Component({ selector: 'app-test-counts-query-host', template: '' })
class CountsHost {
  readonly enabled = signal(true);
  readonly query = injectHubChunkCountsQuery(this.enabled);
}

describe('injectHubChunkCountsQuery', () => {
  const project = signal<string | null>(null);
  let client: QueryClient;
  let urls: URL[];
  let previousFetch: typeof fetch;

  beforeEach(() => {
    project.set(null);
    urls = [];
    previousFetch = globalThis.fetch;
    const fetchStub = async (request: Request): Promise<Response> => {
      const url = new URL(request.url);
      urls.push(url);
      return new Response(JSON.stringify(url.searchParams.has('project') ? PROJECT : ALL), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    };
    hubClient.setConfig({ baseUrl: 'http://localhost', fetch: fetchStub as typeof fetch });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({ providers: [
      provideZonelessChangeDetection(), provideTanStackQuery(client), { provide: HUB_PROJECT_FILTER, useValue: project },
    ] });
  });

  afterEach(() => {
    hubClient.setConfig({ baseUrl: '', fetch: previousFetch });
    client.clear();
  });

  it('selects lane counts while retaining the wire response and its own polling backstop', async () => {
    const fixture = TestBed.createComponent(CountsHost);
    await settle(fixture);
    expect(urls[0].pathname).toBe('/api/chunk-counts');
    expect(urls[0].searchParams.has('project')).toBe(false);
    expect(fixture.componentInstance.query.data()).toEqual({
      statuses: { not_ready: 1, ready: 2, running: 3, delivering: 4, waiting_on_human: 5, needs_human: 6, paused: 7, stopped: 8, done: 9 },
      terminal: 20, total: 99,
    });
    expect(client.getQueryData(lensedKey(hubChunkCountsKey, null))).toEqual(ALL);
    const query = client.getQueryCache().find({ queryKey: lensedKey(hubChunkCountsKey, null), exact: true });
    expect(query?.observers[0].options.refetchInterval).toBe(LIVE_COVERED_POLL_BACKSTOP_MS);
  });

  it('switches lens requests and keeps distinct wire cache entries', async () => {
    const fixture = TestBed.createComponent(CountsHost);
    await settle(fixture);
    project.set('alpha');
    await settle(fixture);
    expect(urls.map((url) => url.searchParams.get('project'))).toEqual([null, 'alpha']);
    expect(fixture.componentInstance.query.data()?.statuses.ready).toBe(12);
    expect(client.getQueryData(lensedKey(hubChunkCountsKey, null))).toEqual(ALL);
    expect(client.getQueryData(lensedKey(hubChunkCountsKey, 'alpha'))).toEqual(PROJECT);
    project.set(null);
    await settle(fixture);
    expect(fixture.componentInstance.query.data()?.statuses.ready).toBe(2);
  });

  it('reactively enables and disables the read', async () => {
    const fixture = TestBed.createComponent(CountsHost);
    fixture.componentInstance.enabled.set(false);
    await settle(fixture);
    expect(urls).toHaveLength(0);
    expect(fixture.componentInstance.query.fetchStatus()).toBe('idle');
    fixture.componentInstance.enabled.set(true);
    await settle(fixture);
    expect(urls).toHaveLength(1);
    fixture.componentInstance.enabled.set(false);
    await settle(fixture);
    await client.invalidateQueries({ queryKey: hubChunksKey });
    await settle(fixture);
    expect(urls).toHaveLength(1);
    fixture.componentInstance.enabled.set(true);
    await settle(fixture);
    expect(urls).toHaveLength(2);
  });

  it('invalidates both lenses under the chunk prefix and refetches the active lens', async () => {
    const fixture = TestBed.createComponent(CountsHost);
    await settle(fixture);
    project.set('alpha');
    await settle(fixture);
    await client.invalidateQueries({ queryKey: hubChunksKey });
    await settle(fixture);
    expect(urls.map((url) => url.searchParams.get('project'))).toEqual([null, 'alpha', 'alpha']);
    expect(client.getQueryState(lensedKey(hubChunkCountsKey, null))?.isInvalidated).toBe(true);
    expect(client.getQueryState(lensedKey(hubChunkCountsKey, 'alpha'))?.isInvalidated).toBe(false);
    project.set(null);
    await settle(fixture);
    expect(urls.map((url) => url.searchParams.get('project'))).toEqual([null, 'alpha', 'alpha', null]);
    expect(client.getQueryState(lensedKey(hubChunkCountsKey, null))?.isInvalidated).toBe(false);
  });
});
