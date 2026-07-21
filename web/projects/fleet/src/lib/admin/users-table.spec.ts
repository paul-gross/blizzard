import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { UserView } from '../api/hub';
import { UsersTable } from './users-table';

const USERS: readonly UserView[] = [
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
    user_id: 'usr_root',
    username: 'root',
    display_name: 'Root',
    email: 'root@example.com',
    role: 'superuser',
    created_at: '2026-07-21T00:00:00Z',
  },
];

describe('UsersTable', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [UsersTable],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  function mount(inputs: Partial<{ users: readonly UserView[]; currentUserId: string | null; isSuperuser: boolean }> = {}) {
    const fixture = TestBed.createComponent(UsersTable);
    fixture.componentRef.setInput('users', inputs.users ?? USERS);
    fixture.componentRef.setInput('currentUserId', inputs.currentUserId ?? null);
    fixture.componentRef.setInput('isSuperuser', inputs.isSuperuser ?? false);
    return fixture;
  }

  it('renders one row per user with its username, email, identities, and role', async () => {
    const fixture = mount();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="users-table-row"]');
    expect(rows).toHaveLength(3);
    const adaRow = el.querySelector('[data-user-id="usr_admin"]');
    expect(adaRow?.querySelector('[data-testid="users-table-username"]')?.textContent).toContain('ada');
    expect(adaRow?.querySelector('[data-testid="users-table-email"]')?.textContent).toContain('ada@example.com');
    expect(adaRow?.querySelector('[data-testid="users-table-identities"]')?.textContent).toContain('github');
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

  it('disables the admin option for a non-superuser actor', async () => {
    const fixture = mount({ currentUserId: 'usr_other', isSuperuser: false });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const guestRow = el.querySelector('[data-user-id="usr_guest"]');
    const adminOption = guestRow?.querySelector<HTMLOptionElement>('option[value="admin"]');
    expect(adminOption?.disabled).toBe(true);
  });

  it('enables the admin option for a superuser actor', async () => {
    const fixture = mount({ currentUserId: 'usr_other', isSuperuser: true });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const guestRow = el.querySelector('[data-user-id="usr_guest"]');
    const adminOption = guestRow?.querySelector<HTMLOptionElement>('option[value="admin"]');
    expect(adminOption?.disabled).toBe(false);
  });

  it('disables the whole selector on an already-admin row for a non-superuser actor (cannot revoke)', async () => {
    const fixture = mount({ currentUserId: 'usr_other', isSuperuser: false });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const adaRow = el.querySelector('[data-user-id="usr_admin"]');
    const select = adaRow?.querySelector<HTMLSelectElement>('[data-testid="users-table-role-select"]');
    expect(select?.disabled).toBe(true);
  });

  it('emits assignRole with the userId and the newly selected role', async () => {
    const fixture = mount({ currentUserId: 'usr_other', isSuperuser: true });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const emitted: { userId: string; role: string }[] = [];
    fixture.componentInstance.assignRole.subscribe((event) => emitted.push(event));

    const guestRow = el.querySelector('[data-user-id="usr_guest"]');
    const select = guestRow?.querySelector<HTMLSelectElement>('[data-testid="users-table-role-select"]');
    expect(select).toBeTruthy();
    select!.value = 'contributor';
    select!.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    expect(emitted).toEqual([{ userId: 'usr_guest', role: 'contributor' }]);
  });
});
