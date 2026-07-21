import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubError, stubRequestClient } from '../testing/stub-request-client';
import { hasPermission, injectMeQuery } from './me.query';

@Component({
  selector: 'fleet-test-me-query-host',
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
    stub = stubRequestClient(hubClient, (method, path) =>
      path === '/api/me'
        ? { user_id: 'usr_1', username: 'op', display_name: 'Op', role: 'contributor', permissions: ['fleet:view'] }
        : {},
    );
    TestBed.configureTestingModule({
      imports: [TestMeQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    });
    const fixture = TestBed.createComponent(TestMeQueryHost);
    await settle(fixture);

    expect(fixture.componentInstance.query.data()).toEqual({
      user_id: 'usr_1',
      username: 'op',
      display_name: 'Op',
      role: 'contributor',
      permissions: ['fleet:view'],
    });
  });

  it('resolves to null (not an error) on 401 — no/expired session is a legitimate value', async () => {
    stub = stubRequestClient(hubClient, (method, path) =>
      path === '/api/me' ? stubError(401, { detail: 'not authenticated' }) : {},
    );
    TestBed.configureTestingModule({
      imports: [TestMeQueryHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
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
    expect(
      hasPermission(
        { user_id: 'u', username: 'u', display_name: 'u', role: 'admin', permissions: ['user:manage'] },
        'user:manage',
      ),
    ).toBe(true);
    expect(
      hasPermission(
        { user_id: 'u', username: 'u', display_name: 'u', role: 'contributor', permissions: ['fleet:view'] },
        'user:manage',
      ),
    ).toBe(false);
  });
});
