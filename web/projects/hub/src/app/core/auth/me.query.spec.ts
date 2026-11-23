import {
  ChangeDetectionStrategy,
  Component,
  provideZonelessChangeDetection,
} from '@angular/core';
import { TestBed } from '@angular/core/testing';
import {
  QueryClient,
  provideTanStackQuery,
} from '@tanstack/angular-query-experimental';

import {
  settle,
  type RequestClientStub,
  stubError,
  stubHubClient,
} from 'fleet/testing';
import { hasPermission, injectMeQuery } from './me.query';
import { session } from './session.builders';

@Component({
  selector: 'app-test-me-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestMeQueryHost {
  readonly query = injectMeQuery();
}

describe('injectMeQuery', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  it('resolves the identity on 200', async () => {
    stub = stubHubClient({
      'GET /api/me': () => ({
        user_id: 'usr_1',
        username: 'op',
        display_name: 'Op',
        role: 'contributor',
        permissions: ['fleet:view'],
        tenants: [],
        hub_admin: false,
        assignable_roles: [],
      }),
    });
    TestBed.configureTestingModule({
      imports: [TestMeQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(
          new QueryClient({ defaultOptions: { queries: { retry: false } } }),
        ),
      ],
    });
    const fixture = TestBed.createComponent(TestMeQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toEqual({
      user_id: 'usr_1',
      username: 'op',
      display_name: 'Op',
      permissions: ['fleet:view'],
      tenants: [],
      tenant_resolved: true,
    });
  });

  it('resolves to null (not an error) on 401 — no/expired session is a legitimate value', async () => {
    stub = stubHubClient({
      'GET /api/me': () => stubError(401, { detail: 'not authenticated' }),
    });
    TestBed.configureTestingModule({
      imports: [TestMeQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(
          new QueryClient({ defaultOptions: { queries: { retry: false } } }),
        ),
      ],
    });
    const fixture = TestBed.createComponent(TestMeQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toBeNull();
    expect(fixture.componentInstance.query.isError()).toBe(false);
  });
});

describe('hasPermission', () => {
  it('is false for null/undefined and true only when the permission is present', () => {
    expect(hasPermission(null, 'user:manage')).toBe(false);
    expect(hasPermission(undefined, 'user:manage')).toBe(false);
    expect(hasPermission(session({ permissions: ['user:manage'] }), 'user:manage')).toBe(true);
    expect(hasPermission(session({ permissions: ['fleet:view'] }), 'user:manage')).toBe(false);
  });
});
