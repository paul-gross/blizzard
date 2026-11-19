import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hostSubscriptionView, settle, stubRunnerClient, type RequestClientStub } from 'fleet/testing';
import { injectHostSubscriptionsQuery } from './host.query';
import { hostSubscriptionsKey } from './query-keys';

@Component({ selector: 'app-test-host-subscriptions', template: '', changeDetection: ChangeDetectionStrategy.OnPush })
class HostSubscriptionsTest {
  readonly first = injectHostSubscriptionsQuery();
  readonly second = injectHostSubscriptionsQuery();
}

describe('host subscriptions read', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('shares one wire cache between projected observers and refetches it on invalidation', async () => {
    let name = 'before';
    stub = stubRunnerClient({
      'GET /api/host/subscriptions': () => ({ items: [hostSubscriptionView({ slug: 's', name, provider: 'p' })] }),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    TestBed.configureTestingModule({
      imports: [HostSubscriptionsTest],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)],
    });
    const fixture = TestBed.createComponent(HostSubscriptionsTest);
    await settle(fixture);
    expect(stub.forRoute('/api/host/subscriptions', 'GET')).toHaveLength(1);
    expect(fixture.componentInstance.first.data()?.[0].name).toBe('before');
    expect(fixture.componentInstance.second.data()?.[0].name).toBe('before');
    expect(client.getQueryData<readonly { name: string }[]>(hostSubscriptionsKey)?.[0].name).toBe('before');
    name = 'after';
    await client.invalidateQueries({ queryKey: hostSubscriptionsKey });
    await settle(fixture);
    expect(stub.forRoute('/api/host/subscriptions', 'GET')).toHaveLength(2);
    expect(fixture.componentInstance.first.data()?.[0].name).toBe('after');
    expect(fixture.componentInstance.second.data()?.[0].name).toBe('after');
  });
});
