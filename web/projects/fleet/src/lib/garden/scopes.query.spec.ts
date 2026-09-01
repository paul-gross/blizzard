import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { settle } from '../testing/settle';
import { injectHubScopesQuery } from './scopes.query';

@Component({
  selector: 'fleet-test-scopes-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestScopesQueryHost {
  readonly query = injectHubScopesQuery();
}

describe('injectHubScopesQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('reads the scope list off GET /api/scopes', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/scopes') {
        return [{ slug: 'blizzard', description: 'the hub itself', created_at: '2026-01-01T00:00:00Z', retired: false }];
      }
      return {};
    });
    TestBed.configureTestingModule({
      imports: [TestScopesQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestScopesQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toHaveLength(1);
    expect(stub.forRoute('/api/scopes', 'GET')).toHaveLength(1);
  });
});
