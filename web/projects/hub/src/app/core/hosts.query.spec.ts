import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hostRegistryView, settle, stubHubClient, type RequestClientStub } from 'fleet/testing';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';
import { hostQueryOptions, injectHostFactsQuery } from './hosts.query';
import { hubHostsKey } from './query-keys';

@Component({ selector: 'app-test-hosts', template: '', changeDetection: ChangeDetectionStrategy.OnPush })
class HostsTest {
  readonly active = injectHostFactsQuery();
  readonly all = injectHostFactsQuery(() => true);
  readonly second = injectHostFactsQuery(() => true);
}

describe('host registry query variants', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('separates retired variants, shares their cache, and invalidates both under the common key', async () => {
    let version = 1;
    stub = stubHubClient({ 'GET /api/hosts': (request) => ({ hosts: [
      hostRegistryView({ host_id: `active-${version}` }),
      ...(request.search.includes('include_retired=true') ? [hostRegistryView({ host_id: 'retired', retired: true })] : []),
    ] }) });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({ imports: [HostsTest], providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)] });
    const fixture = TestBed.createComponent(HostsTest);
    await settle(fixture);
    expect(stub.forRoute('/api/hosts', 'GET')).toHaveLength(2);
    expect(fixture.componentInstance.active.data()?.map((h) => h.host_id)).toEqual(['active-1']);
    expect(fixture.componentInstance.all.data()?.map((h) => h.host_id)).toEqual(['active-1', 'retired']);
    expect(fixture.componentInstance.second.data()?.[1].retired).toBe(true);
    expect(hostQueryOptions(() => false).refetchInterval).toBe(LIVE_COVERED_POLL_BACKSTOP_MS);
    expect(hostQueryOptions(() => true).refetchInterval).toBe(LIVE_COVERED_POLL_BACKSTOP_MS);
    version = 2;
    await client.invalidateQueries({ queryKey: hubHostsKey });
    await settle(fixture);
    expect(stub.forRoute('/api/hosts', 'GET')).toHaveLength(4);
    expect(fixture.componentInstance.active.data()?.[0].host_id).toBe('active-2');
    expect(fixture.componentInstance.second.data()?.[0].host_id).toBe('active-2');
  });
});
