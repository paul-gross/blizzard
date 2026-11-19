import { memberWire } from './members-fixture.translate';
import { member } from './members.builders';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { Member } from './members.domain';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubHubClient } from 'fleet/testing';

import { MembersPage } from './members-page';
import { hubMembersKey } from '../../core/query-keys';

const ACME = { tenant_id: 'ten_acme', name: 'Acme', role: 'admin' as const, permissions: ['user:manage' as const] };

const ME_ADMIN = {
  ...OPERATOR_ME_RESPONSE,
  user_id: 'usr_admin',
  username: 'ada',
  display_name: 'Ada',
  role: 'admin' as const,
  permissions: ['user:manage' as const],
  hub_admin: false,
  tenants: [ACME],
};

const MEMBERS: Member[] = [
  member({
    userId: 'usr_admin',
    username: 'ada',
    displayName: 'Ada',
    email: 'ada@example.com',
    role: 'admin',
    hubAdmin: false,
    createdAt: '2026-07-21T00:00:00Z',
    identities: [],
    assignableRoles: [],
    revocable: false,
  }),
  member({
    userId: 'usr_guest',
    username: 'grace',
    displayName: 'Grace',
    email: null,
    role: 'guest',
    hubAdmin: false,
    createdAt: '2026-07-21T00:00:00Z',
    identities: [],
    assignableRoles: ['guest', 'contributor', 'admin'],
    revocable: true,
  }),
];

type Routes = Parameters<typeof stubHubClient>[0];
type MembersAnswer = NonNullable<Routes['GET /api/members']>;

describe('MembersPage', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    sessionStorage.clear();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function mount(me: typeof ME_ADMIN, members: MembersAnswer, routes: Routes = {}) {
    stub = stubHubClient({
      'GET /api/me': () => me,
      'GET /api/members': members,
      'PUT /api/members/{user_id}': (_request, { user_id }) => memberWire({ ...MEMBERS[1], userId: user_id }),
      'DELETE /api/members/{user_id}': () => undefined,
      ...routes,
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
    const { el } = await mount(ME_ADMIN, () => MEMBERS.map(memberWire));

    expect(el.querySelectorAll('[data-testid="members-table-row"]')).toHaveLength(2);
    expect(stub.forRoute('/api/members', 'GET')).toHaveLength(1);
    expect(stub.forRoute('/api/users', 'GET')).toHaveLength(0);
  });

  it('labels the table Members', async () => {
    const { el } = await mount(ME_ADMIN, () => MEMBERS.map(memberWire));

    expect(el.querySelector('[data-testid="members-table"]')?.getAttribute('aria-label')).toBe('Members');
  });

  it('keeps the labeled panel for an empty listing', async () => {
    const { el } = await mount(ME_ADMIN, () => []);

    const panel = el.querySelector('[data-testid="members-table"]');
    expect(panel?.getAttribute('aria-label')).toBe('Members');
    expect(panel?.querySelector('[data-testid="members-table-empty"]')).toBeTruthy();
  });

  it('keeps a refused change visible when the listing becomes empty', async () => {
    const { fixture, el } = await mount(ME_ADMIN, () => MEMBERS.map(memberWire), {
      'PUT /api/members/{user_id}': () => stubError(409, { detail: 'refused' }),
    });
    await choose(fixture, el, 'usr_guest', 'contributor');
    await settle(fixture);
    TestBed.inject(QueryClient).setQueryData(hubMembersKey, []);
    await settle(fixture);

    expect(el.querySelector('[data-testid="members-table-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="members-page-change-error"]')?.textContent).toContain('refused');
  });

  it('keeps a pending confirmation when the listing becomes empty', async () => {
    const { fixture, el } = await mount(ME_ADMIN, () => MEMBERS.map(memberWire));
    el.querySelector<HTMLElement>('[data-user-id="usr_guest"] [data-testid="members-table-revoke"]')!.click();
    await settle(fixture);
    TestBed.inject(QueryClient).setQueryData(hubMembersKey, []);
    await settle(fixture);

    expect(el.querySelector('[data-testid="members-table-empty"]')).toBeTruthy();
    expect(document.querySelector('[data-testid="members-page-revoke-confirm"]')).toBeTruthy();
  });

  it('renders an error state when the listing is refused (403, below user:manage)', async () => {
    const { el } = await mount(ME_ADMIN, () => stubError(403, { detail: "missing permission 'user:manage'" }));

    expect(el.querySelector('[data-testid="members-table"] [data-testid="members-page-error"]')).toBeTruthy();
  });

  it("keeps the actor's own row read-only", async () => {
    const { el } = await mount(ME_ADMIN, () => MEMBERS.map(memberWire));

    const own = el.querySelector('[data-user-id="usr_admin"]');
    expect(own?.querySelector('[data-testid="members-table-role-static"]')?.textContent).toContain('(you)');
    expect(own?.querySelector('[data-testid="members-table-revoke"]')).toBeNull();
  });

  it('changes a role with PUT /api/members/{id}', async () => {
    const { fixture, el } = await mount(ME_ADMIN, () => MEMBERS.map(memberWire), {
      'PUT /api/members/{user_id}': () => memberWire({ ...MEMBERS[1], role: 'contributor' }),
    });

    await choose(fixture, el, 'usr_guest', 'contributor');
    await settle(fixture);

    const puts = stub.forRoute('/api/members/usr_guest', 'PUT');
    expect(puts).toHaveLength(1);
    expect(puts[0].body).toEqual({ role: 'contributor' });
    expect(stub.forRoute('/api/users/usr_guest/role', 'POST')).toHaveLength(0);
  });

  it('surfaces a refused role change instead of appearing to succeed', async () => {
    const { fixture, el } = await mount(ME_ADMIN, () => MEMBERS.map(memberWire), {
      'PUT /api/members/{user_id}': () => stubError(409, { detail: 'a tenant keeps at least one admin' }),
    });

    await choose(fixture, el, 'usr_guest', 'contributor');
    await settle(fixture);

    expect(el.querySelector('[data-testid="members-page-change-error"]')?.textContent).toContain(
      'a tenant keeps at least one admin',
    );
  });

  it('revokes with DELETE /api/members/{id} only once confirmed', async () => {
    const { fixture, el } = await mount(ME_ADMIN, () => MEMBERS.map(memberWire));

    el.querySelector<HTMLElement>('[data-user-id="usr_guest"] [data-testid="members-table-revoke"]')!.click();
    await settle(fixture);
    expect(stub.forRoute('/api/members/usr_guest', 'DELETE')).toHaveLength(0);
    expect(document.querySelector('[data-testid="members-page-revoke-confirm"]')?.textContent).toContain('Grace (grace)');
    expect(document.querySelector('[data-testid="members-page-revoke-confirm"]')?.textContent).toContain('this tenant');

    document.querySelector<HTMLElement>('[data-testid="members-page-revoke-confirm-confirm"]')!.click();
    await settle(fixture);

    expect(stub.forRoute('/api/members/usr_guest', 'DELETE')).toHaveLength(1);
    expect(document.querySelector('[data-testid="members-page-revoke-confirm"]')).toBeNull();
  });

  it('cancelling the confirmation revokes nothing', async () => {
    const { fixture, el } = await mount(ME_ADMIN, () => MEMBERS.map(memberWire));

    el.querySelector<HTMLElement>('[data-user-id="usr_guest"] [data-testid="members-table-revoke"]')!.click();
    await settle(fixture);
    document.querySelector<HTMLElement>('[data-testid="members-page-revoke-confirm-cancel"]')!.click();
    await settle(fixture);

    expect(stub.forRoute('/api/members/usr_guest', 'DELETE')).toHaveLength(0);
    expect(document.querySelector('[data-testid="members-page-revoke-confirm"]')).toBeNull();
  });

  it('surfaces a refused revocation', async () => {
    const { fixture, el } = await mount(ME_ADMIN, () => MEMBERS.map(memberWire), {
      'DELETE /api/members/{user_id}': () => stubError(409, { detail: 'a tenant keeps at least one admin' }),
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
