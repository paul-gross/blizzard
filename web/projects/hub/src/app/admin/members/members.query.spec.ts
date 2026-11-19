import { memberWire } from './members-fixture.translate';
import { member } from './members.builders';
import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { settle, type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';
import { hubMembersKey } from '../../core/query-keys';
import { injectMembersQuery } from './members.query';

@Component({
  selector: 'app-test-members-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestMembersQueryHost {
  readonly query = injectMembersQuery();
}

@Component({ selector: 'app-test-disabled-members-query-host', changeDetection: ChangeDetectionStrategy.OnPush, template: '' })
class TestDisabledMembersQueryHost {
  readonly query = injectMembersQuery(() => false);
}

const MEMBERS = [
  member({
    userId: 'usr_1',
    username: 'ada',
    displayName: 'Ada',
    email: 'ada@example.com',
    role: 'admin',
    hubAdmin: false,
    createdAt: '2026-07-21T00:00:00Z',
    identities: [{ providerName: 'github', handle: 'ada' }],
    assignableRoles: [],
    revocable: false,
  }),
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
    stub = stubHubClient({ 'GET /api/members': () => MEMBERS.map(memberWire) });
    const fixture = mount();
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toEqual(MEMBERS);
    expect(TestBed.inject(QueryClient).getQueryData(hubMembersKey)).toEqual(MEMBERS.map(memberWire));
    expect(stub.forRoute('/api/users', 'GET')).toHaveLength(0);
  });

  it('does not read members when disabled', async () => {
    stub = stubHubClient({ 'GET /api/members': () => MEMBERS.map(memberWire) });
    TestBed.configureTestingModule({
      imports: [TestDisabledMembersQueryHost],
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient())],
    });
    const fixture = TestBed.createComponent(TestDisabledMembersQueryHost);
    await settle(fixture);
    expect(stub.forRoute('/api/members', 'GET')).toHaveLength(0);
  });

  it('surfaces a 403 (below user:manage) as the query error state', async () => {
    stub = stubHubClient({
      'GET /api/members': () => stubError(403, { detail: "missing permission 'user:manage'" }),
    });
    const fixture = mount();
    await settle(fixture);

    expect(fixture.componentInstance.query.isError()).toBe(true);
  });
});
