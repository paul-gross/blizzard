import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { AdminPage } from './admin-page';

const ME_ADMIN = {
  user_id: 'usr_admin',
  username: 'ada',
  display_name: 'Ada',
  role: 'admin',
  permissions: ['user:manage'],
  assignable_roles: OPERATOR_ME_RESPONSE.assignable_roles,
};

const ME_SUPERUSER = { ...ME_ADMIN, user_id: 'usr_root', username: 'root', role: 'superuser' };

const USERS = [
  {
    user_id: 'usr_admin',
    username: 'ada',
    display_name: 'Ada',
    email: 'ada@example.com',
    role: 'admin',
    created_at: '2026-07-21T00:00:00Z',
    identities: [],
  },
  {
    user_id: 'usr_guest',
    username: 'grace',
    display_name: 'Grace',
    email: null,
    role: 'guest',
    created_at: '2026-07-21T00:00:00Z',
    identities: [],
    assignable_roles: ['pending', 'guest', 'contributor'],
  },
];

// The same rows as a superuser actor's listing renders them.
const USERS_FOR_SUPERUSER = USERS.map((user) => ({
  ...user,
  assignable_roles: ['pending', 'guest', 'contributor', 'admin'],
}));

describe('AdminPage', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function mount(me: unknown, users: unknown) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (path === '/api/me') return me;
      if (path === '/api/users') return users;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [AdminPage],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(AdminPage);
    await settle(fixture);
    return fixture;
  }

  it('renders the user table once both reads settle', async () => {
    const fixture = await mount(ME_ADMIN, USERS);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('app-users-table')).toBeTruthy();
    expect(el.querySelectorAll('[data-testid="users-table-row"]')).toHaveLength(2);
  });

  it('renders an error state when the users read is refused (403, below user:manage)', async () => {
    const fixture = await mount(ME_ADMIN, stubError(403, { detail: "missing permission 'user:manage'" }));
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="admin-page-error"]')).toBeTruthy();
  });

  it("enables the roles the hub's listing offers the actor on each row (admin option enabled)", async () => {
    const fixture = await mount(ME_SUPERUSER, USERS_FOR_SUPERUSER);
    const el = fixture.nativeElement as HTMLElement;

    const guestRow = el.querySelector('[data-user-id="usr_guest"]');
    guestRow?.querySelector<HTMLButtonElement>('[data-testid="users-table-role-select"]')?.click();
    await settle(fixture);
    expect(document.querySelector('[role="listbox"] [role="option"][title="admin"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it("renders the signed-in actor's own row as read-only", async () => {
    const fixture = await mount(ME_ADMIN, USERS);
    const el = fixture.nativeElement as HTMLElement;

    const ownRow = el.querySelector('[data-user-id="usr_admin"]');
    expect(ownRow?.querySelector('[data-testid="users-table-role-static"]')?.textContent).toContain('(you)');
  });

  it('surfaces a refused role change instead of appearing to succeed', async () => {
    const fixture = await mount(ME_ADMIN, USERS);
    stub.restore();
    stub = stubRequestClient(hubClient, (method, path) =>
      path === '/api/users/usr_guest/role' ? stubError(403, { detail: 'only superuser may grant or revoke admin' }) : USERS,
    );
    const el = fixture.nativeElement as HTMLElement;

    const select = el.querySelector<HTMLSelectElement>(
      '[data-user-id="usr_guest"] [data-testid="users-table-role-select"]',
    );
    select!.click();
    await settle(fixture);
    document.querySelector<HTMLElement>('[role="listbox"] [role="option"][title="contributor"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="admin-page-assign-role-error"]')?.textContent).toContain(
      'only superuser may grant or revoke admin',
    );
  });
});
