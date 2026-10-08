import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { UserView } from 'fleet';
import { OPERATOR_ME_RESPONSE } from 'fleet/testing';
import { UsersTable } from './users-table';

const BASE_USERS: readonly UserView[] = [
  {
    user_id: 'usr_admin',
    username: 'ada',
    display_name: 'Ada',
    email: 'ada@example.com',
    role: 'admin',
    created_at: '2026-07-21T00:00:00Z',
    identities: [{ provider_name: 'github', handle: 'ada' }],
  },
  {
    user_id: 'usr_guest',
    username: 'grace',
    display_name: 'Grace',
    email: null,
    role: 'guest',
    created_at: '2026-07-21T00:00:00Z',
    identities: [],
  },
  {
    user_id: 'usr_pending',
    username: 'newcomer',
    display_name: 'Newcomer',
    email: null,
    role: 'pending',
    created_at: '2026-07-21T00:00:00Z',
    identities: [],
  },
  {
    user_id: 'usr_root',
    username: 'root',
    display_name: 'Root',
    email: 'root@example.com',
    role: 'superuser',
    created_at: '2026-07-21T00:00:00Z',
  },
];

type Actor = 'admin' | 'superuser';

/** Each row as `GET /api/users` renders it for `actor` — the hub's own per-row `assignable_roles`. */
function usersFor(actor: Actor): readonly UserView[] {
  return BASE_USERS.map((user) => {
    if (user.role === 'superuser') return { ...user, assignable_roles: [] };
    if (actor === 'superuser') return { ...user, assignable_roles: ['pending', 'guest', 'contributor', 'admin'] };
    if (user.role === 'admin') return { ...user, assignable_roles: [] };
    return { ...user, assignable_roles: ['pending', 'guest', 'contributor'] };
  });
}

const USERS = usersFor('admin');

describe('UsersTable', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [UsersTable],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  function mount(inputs: Partial<{ users: readonly UserView[]; currentUserId: string | null; actor: Actor }> = {}) {
    const fixture = TestBed.createComponent(UsersTable);
    fixture.componentRef.setInput('users', inputs.users ?? usersFor(inputs.actor ?? 'admin'));
    fixture.componentRef.setInput('currentUserId', inputs.currentUserId ?? null);
    fixture.componentRef.setInput('assignableRoles', OPERATOR_ME_RESPONSE.assignable_roles ?? []);
    return fixture;
  }

  it('renders one row per user with its username, email, identities, and role', async () => {
    const fixture = mount();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="users-table-row"]');
    expect(rows).toHaveLength(4);
    const adaRow = el.querySelector('[data-user-id="usr_admin"]');
    expect(adaRow?.querySelector('[data-testid="users-table-username"]')?.textContent).toContain('ada');
    expect(adaRow?.querySelector('[data-testid="users-table-email"]')?.textContent).toContain('ada@example.com');
    expect(adaRow?.querySelector('[data-testid="users-table-identities"]')?.textContent).toContain('github');
  });

  it('renders the created column via the shared relative-date component, not a raw ISO string', async () => {
    const fixture = mount();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const adaRow = el.querySelector('[data-user-id="usr_admin"]');
    const when = adaRow?.querySelector('fleet-when[data-testid="users-table-created-at"]');
    expect(when).toBeTruthy();
    expect(when?.textContent?.trim()).not.toBe(USERS[0].created_at);
  });

  it('shows an empty state with no users', async () => {
    const fixture = mount({ users: [] });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="users-table-empty"]')).toBeTruthy();
  });

  it('renders a superuser row as static text, never a selector — bootstrap-only', async () => {
    const fixture = mount();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rootRow = el.querySelector('[data-user-id="usr_root"]');
    expect(rootRow?.querySelector('[data-testid="users-table-role-static"]')?.textContent).toContain('superuser');
    expect(rootRow?.querySelector('[data-testid="users-table-role-select"]')).toBeNull();
  });

  it("renders the signed-in actor's own row as static text — self-change refused", async () => {
    const fixture = mount({ currentUserId: 'usr_admin' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const adaRow = el.querySelector('[data-user-id="usr_admin"]');
    expect(adaRow?.querySelector('[data-testid="users-table-role-static"]')?.textContent).toContain('(you)');
    expect(adaRow?.querySelector('[data-testid="users-table-role-select"]')).toBeNull();
  });

  it('renders four role options in order for an ordinary row', async () => {
    const fixture = mount({ currentUserId: 'usr_other', actor: 'superuser' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const guestRow = el.querySelector('[data-user-id="usr_guest"]');
    guestRow?.querySelector<HTMLButtonElement>('[data-testid="users-table-role-select"]')?.click();
    await fixture.whenStable();
    expect(Array.from(document.querySelectorAll('[role="listbox"] [role="option"]')).map((o) => o.textContent?.trim())).toEqual(['pending', 'guest', 'contributor', 'admin']);
  });

  it("selects the row's current role by default, for every assignable role", async () => {
    const fixture = mount({ currentUserId: 'usr_other', actor: 'superuser' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const guestSelect = el.querySelector<HTMLSelectElement>('[data-user-id="usr_guest"] [data-testid="users-table-role-select"]');
    expect(guestSelect?.getAttribute('aria-label')).toBe('Role for grace: guest');
    const pendingSelect = el.querySelector<HTMLSelectElement>(
      '[data-user-id="usr_pending"] [data-testid="users-table-role-select"]',
    );
    expect(pendingSelect?.getAttribute('aria-label')).toBe('Role for newcomer: pending');
  });

  it("disables only the selector of a row whose own assignment is in flight", async () => {
    const fixture = mount({ currentUserId: 'usr_other', actor: 'superuser' });
    fixture.componentRef.setInput('pendingUserIds', ['usr_pending']);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-user-id="usr_pending"] [data-testid="users-table-role-select"]')?.disabled).toBe(true);
    const others = Array.from(el.querySelectorAll<HTMLButtonElement>('[data-testid="users-table-role-select"]')).filter(
      (select) => select.closest('tr')?.getAttribute('data-user-id') !== 'usr_pending',
    );
    expect(others.length).toBeGreaterThan(0);
    expect(others.some((select) => !select.disabled)).toBe(true);
  });

  it('renders a pending row with an enabled selector, not as static text', async () => {
    const fixture = mount({ currentUserId: 'usr_other', actor: 'superuser' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const pendingRow = el.querySelector('[data-user-id="usr_pending"]');
    const select = pendingRow?.querySelector<HTMLSelectElement>('[data-testid="users-table-role-select"]');
    expect(select).toBeTruthy();
    expect(select?.disabled).toBe(false);
    expect(pendingRow?.querySelector('[data-testid="users-table-role-static"]')).toBeNull();

    select?.click();
    await fixture.whenStable();
    expect(document.querySelector('[role="listbox"] [role="option"][title="admin"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('disables the admin option a row does not offer the actor', async () => {
    const fixture = mount({ currentUserId: 'usr_other', actor: 'admin' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const guestRow = el.querySelector('[data-user-id="usr_guest"]');
    guestRow?.querySelector<HTMLButtonElement>('[data-testid="users-table-role-select"]')?.click();
    await fixture.whenStable();
    expect(document.querySelector('[role="listbox"] [role="option"][title="admin"]')?.getAttribute('aria-disabled')).toBe('true');
  });

  it('enables the admin option for a superuser actor', async () => {
    const fixture = mount({ currentUserId: 'usr_other', actor: 'superuser' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const guestRow = el.querySelector('[data-user-id="usr_guest"]');
    guestRow?.querySelector<HTMLButtonElement>('[data-testid="users-table-role-select"]')?.click();
    await fixture.whenStable();
    expect(document.querySelector('[role="listbox"] [role="option"][title="admin"]')?.getAttribute('aria-disabled')).not.toBe('true');
  });

  it('disables the whole selector on a row that offers the actor no role', async () => {
    const fixture = mount({ currentUserId: 'usr_other', actor: 'admin' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const adaRow = el.querySelector('[data-user-id="usr_admin"]');
    const select = adaRow?.querySelector<HTMLSelectElement>('[data-testid="users-table-role-select"]');
    expect(select?.disabled).toBe(true);
  });

  it('emits assignRole with the userId and the newly selected role', async () => {
    const fixture = mount({ currentUserId: 'usr_other', actor: 'superuser' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const emitted: { userId: string; role: string }[] = [];
    fixture.componentInstance.assignRole.subscribe((event) => emitted.push(event));

    const guestRow = el.querySelector('[data-user-id="usr_guest"]');
    const select = guestRow?.querySelector<HTMLSelectElement>('[data-testid="users-table-role-select"]');
    expect(select).toBeTruthy();
    select!.click();
    await fixture.whenStable();
    document.querySelector<HTMLElement>('[role="listbox"] [role="option"][title="contributor"]')?.click();
    await fixture.whenStable();

    expect(emitted).toEqual([{ userId: 'usr_guest', role: 'contributor' }]);
  });
});
