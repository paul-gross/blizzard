import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { TAB_TENANT_KEY } from '../../core/auth/tenant-context';
import { MembersPage } from './members-page';

const ACME = { tenant_id: 'ten_acme', name: 'Acme', role: 'admin', permissions: ['user:manage'] };
const GLOBEX = { tenant_id: 'ten_globex', name: 'Globex', role: 'admin', permissions: ['user:manage'] };

const ME_ADMIN = {
  user_id: 'usr_admin',
  username: 'ada',
  display_name: 'Ada',
  role: 'admin',
  permissions: ['user:manage'],
  hub_admin: false,
  tenants: [ACME],
};

const MEMBERS = [
  {
    user_id: 'usr_admin',
    username: 'ada',
    display_name: 'Ada',
    email: 'ada@example.com',
    role: 'admin',
    hub_admin: false,
    created_at: '2026-07-21T00:00:00Z',
    identities: [],
    assignable_roles: [],
    revocable: false,
  },
  {
    user_id: 'usr_guest',
    username: 'grace',
    display_name: 'Grace',
    email: null,
    role: 'guest',
    hub_admin: false,
    created_at: '2026-07-21T00:00:00Z',
    identities: [],
    assignable_roles: ['guest', 'contributor', 'admin'],
    revocable: true,
  },
];

type Answer = unknown | ((method: string) => unknown);

describe('MembersPage', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    sessionStorage.clear();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function mount(me: unknown, members: unknown, routes: Record<string, Answer> = {}) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return me;
      if (path === '/api/members') return members;
      const answer = routes[path];
      if (answer !== undefined) return typeof answer === 'function' ? (answer as (m: string) => unknown)(method) : answer;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [MembersPage],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(MembersPage);
    await settle(fixture);
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  async function choose(fixture: { whenStable(): Promise<unknown> }, el: HTMLElement, userId: string, role: string) {
    el.querySelector<HTMLElement>(`[data-user-id="${userId}"] [data-testid="members-table-role-select"]`)!.click();
    await fixture.whenStable();
    document.querySelector<HTMLElement>(`[role="listbox"] [role="option"][title="${role}"]`)!.click();
  }

  it("lists the tenant's members from GET /api/members, never the users route", async () => {
    const { el } = await mount(ME_ADMIN, MEMBERS);

    expect(el.querySelectorAll('[data-testid="members-table-row"]')).toHaveLength(2);
    expect(stub.forRoute('/api/members', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/users', 'GET')).toHaveLength(0);
  });

  it('names the tenant it is opened in', async () => {
    const { el } = await mount(ME_ADMIN, MEMBERS);

    expect(el.querySelector('[data-testid="members-table"]')?.getAttribute('aria-label')).toBe('Members of Acme');
  });

  it('names the tab\'s tenant among several memberships', async () => {
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_globex');
    const { el } = await mount({ ...ME_ADMIN, tenants: [ACME, GLOBEX] }, MEMBERS);

    expect(el.querySelector('[data-testid="members-table"]')?.getAttribute('aria-label')).toBe('Members of Globex');
  });

  it('renders an error state when the listing is refused (403, below user:manage)', async () => {
    const { el } = await mount(ME_ADMIN, stubError(403, { detail: "missing permission 'user:manage'" }));

    expect(el.querySelector('[data-testid="members-page-error"]')).toBeTruthy();
  });

  it("keeps the actor's own row read-only", async () => {
    const { el } = await mount(ME_ADMIN, MEMBERS);

    const own = el.querySelector('[data-user-id="usr_admin"]');
    expect(own?.querySelector('[data-testid="members-table-role-static"]')?.textContent).toContain('(you)');
    expect(own?.querySelector('[data-testid="members-table-revoke"]')).toBeNull();
  });

  it('changes a role with PUT /api/members/{id}', async () => {
    const { fixture, el } = await mount(ME_ADMIN, MEMBERS, {
      '/api/members/usr_guest': (method: string) => (method === 'PUT' ? { ...MEMBERS[1], role: 'contributor' } : {}),
    });

    await choose(fixture, el, 'usr_guest', 'contributor');
    await settle(fixture);

    const puts = stub.forRoute('/api/members/usr_guest', 'PUT');
    expect(puts).toHaveLength(1);
    expect(puts[0].body).toEqual({ role: 'contributor' });
    expect(stub.forRoute('/api/users/usr_guest/role', 'POST')).toHaveLength(0);
  });

  it('surfaces a refused role change instead of appearing to succeed', async () => {
    const { fixture, el } = await mount(ME_ADMIN, MEMBERS, {
      '/api/members/usr_guest': stubError(409, { detail: 'a tenant keeps at least one admin' }),
    });

    await choose(fixture, el, 'usr_guest', 'contributor');
    await settle(fixture);

    expect(el.querySelector('[data-testid="members-page-change-error"]')?.textContent).toContain(
      'a tenant keeps at least one admin',
    );
  });

  it('revokes with DELETE /api/members/{id} only once confirmed', async () => {
    const { fixture, el } = await mount(ME_ADMIN, MEMBERS);

    el.querySelector<HTMLElement>('[data-user-id="usr_guest"] [data-testid="members-table-revoke"]')!.click();
    await settle(fixture);
    expect(stub.forRoute('/api/members/usr_guest', 'DELETE')).toHaveLength(0);
    expect(document.querySelector('[data-testid="members-page-revoke-confirm"]')?.textContent).toContain('Grace (grace)');
    expect(document.querySelector('[data-testid="members-page-revoke-confirm"]')?.textContent).toContain('Acme');

    document.querySelector<HTMLElement>('[data-testid="members-page-revoke-confirm-confirm"]')!.click();
    await settle(fixture);

    expect(stub.forRoute('/api/members/usr_guest', 'DELETE')).toHaveLength(1);
    expect(document.querySelector('[data-testid="members-page-revoke-confirm"]')).toBeNull();
  });

  it('cancelling the confirmation revokes nothing', async () => {
    const { fixture, el } = await mount(ME_ADMIN, MEMBERS);

    el.querySelector<HTMLElement>('[data-user-id="usr_guest"] [data-testid="members-table-revoke"]')!.click();
    await settle(fixture);
    document.querySelector<HTMLElement>('[data-testid="members-page-revoke-confirm-cancel"]')!.click();
    await settle(fixture);

    expect(stub.forRoute('/api/members/usr_guest', 'DELETE')).toHaveLength(0);
    expect(document.querySelector('[data-testid="members-page-revoke-confirm"]')).toBeNull();
  });

  it('surfaces a refused revocation', async () => {
    const { fixture, el } = await mount(ME_ADMIN, MEMBERS, {
      '/api/members/usr_guest': (method: string) =>
        method === 'DELETE' ? stubError(409, { detail: 'a tenant keeps at least one admin' }) : {},
    });

    el.querySelector<HTMLElement>('[data-user-id="usr_guest"] [data-testid="members-table-revoke"]')!.click();
    await settle(fixture);
    document.querySelector<HTMLElement>('[data-testid="members-page-revoke-confirm-confirm"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="members-page-change-error"]')?.textContent).toContain(
      'a tenant keeps at least one admin',
    );
  });
});
