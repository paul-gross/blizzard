import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { hubClient } from 'fleet';
import { settle, type RequestClientStub, stubError, stubRequestClient } from 'fleet/testing';
import { injectMembersQuery } from './members.query';

@Component({
  selector: 'app-test-members-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestMembersQueryHost {
  readonly query = injectMembersQuery();
}

const MEMBERS = [
  {
    user_id: 'usr_1',
    username: 'ada',
    display_name: 'Ada',
    email: 'ada@example.com',
    role: 'admin',
    hub_admin: false,
    created_at: '2026-07-21T00:00:00Z',
    identities: [{ provider_name: 'github', handle: 'ada' }],
    assignable_roles: [],
    revocable: false,
  },
];

describe('injectMembersQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  function mount() {
    TestBed.configureTestingModule({
      imports: [TestMembersQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    });
    return TestBed.createComponent(TestMembersQueryHost);
  }

  it('reads GET /api/members and resolves its rows', async () => {
    stub = stubRequestClient(hubClient, (method, path) => (path === '/api/members' ? MEMBERS : {}));
    const fixture = mount();
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toEqual(MEMBERS);
    expect(stub.forRoute('/api/users', 'GET')).toHaveLength(0);
  });

  it('surfaces a 403 (below user:manage) as the query error state', async () => {
    stub = stubRequestClient(hubClient, (method, path) =>
      path === '/api/members' ? stubError(403, { detail: "missing permission 'user:manage'" }) : {},
    );
    const fixture = mount();
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
  });
});
