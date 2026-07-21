import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { injectUsersQuery } from './users.query';

@Component({
  selector: 'fleet-test-users-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestUsersQueryHost {
  readonly query = injectUsersQuery();
}

const USERS = [
  {
    user_id: 'usr_1',
    username: 'ada',
    display_name: 'Ada',
    email: 'ada@example.com',
    role: 'admin',
    created_at: '2026-07-21T00:00:00Z',
    identities: [{ provider_name: 'github', handle: 'ada' }],
  },
];

describe('injectUsersQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  function mount() {
    TestBed.configureTestingModule({
      imports: [TestUsersQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    });
    return TestBed.createComponent(TestUsersQueryHost);
  }

  it('resolves the user list on 200', async () => {
    stub = stubRequestClient(hubClient, (method, path) => (path === '/api/users' ? USERS : {}));
    const fixture = mount();
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toEqual(USERS);
  });

  it('surfaces a 403 (below user:manage) as the query error state', async () => {
    stub = stubRequestClient(hubClient, (method, path) =>
      path === '/api/users' ? stubError(403, { detail: "missing permission 'user:manage'" }) : {},
    );
    const fixture = mount();
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
  });
});
