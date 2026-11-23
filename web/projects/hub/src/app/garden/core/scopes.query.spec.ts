import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { gardenScopeView, type RequestClientStub, stubHubClient, settle } from 'fleet/testing';
import { injectHubDomainScopesQuery } from './scopes.query';

@Component({
  selector: 'app-test-scopes-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestScopesQueryHost {
  readonly query = injectHubDomainScopesQuery();
}

describe('injectHubDomainScopesQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads the scope list off GET /api/scopes', async () => {
    stub = stubHubClient({
      'GET /api/scopes': () => [gardenScopeView({ slug: 'blizzard', description: 'the hub itself' })],
    });
    TestBed.configureTestingModule({
      imports: [TestScopesQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestScopesQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toMatchObject([{ slug: 'blizzard', description: 'the hub itself' }]);
    expect(stub.forRoute('/api/scopes', 'GET')).toHaveLength(1);
  });
});
