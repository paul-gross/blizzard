import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import { injectAuthProvidersQuery } from './providers.query';

@Component({
  selector: 'fleet-test-providers-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestProvidersQueryHost {
  readonly query = injectAuthProvidersQuery();
}

describe('injectAuthProvidersQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('lists the configured providers, empty under auth.mode=none', async () => {
    stub = stubRequestClient(hubClient, (method, path) => (path === '/api/auth/providers' ? [] : {}));
    TestBed.configureTestingModule({
      imports: [TestProvidersQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestProvidersQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toEqual([]);
  });

  it('resolves the provider list on success', async () => {
    stub = stubRequestClient(hubClient, (method, path) =>
      path === '/api/auth/providers' ? [{ name: 'github', display_name: 'GitHub', type: 'github' }] : {},
    );
    TestBed.configureTestingModule({
      imports: [TestProvidersQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestProvidersQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toEqual([{ name: 'github', display_name: 'GitHub', type: 'github' }]);
  });
});
