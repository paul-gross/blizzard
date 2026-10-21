import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { MemberView } from 'fleet';
import { MembersTable } from './members-table';

/** The rows of `GET /api/members` as a tenant admin (`usr_admin`) reads them: the hub's own per-row
 * `assignable_roles` and `revocable`. */
const MEMBERS: readonly MemberView[] = [
  {
    user_id: 'usr_admin',
    username: 'ada',
    display_name: 'Ada',
    email: 'ada@example.com',
    role: 'admin',
    hub_admin: true,
    created_at: '2026-07-21T00:00:00Z',
    identities: [{ provider_name: 'github', handle: 'ada' }],
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
  {
    user_id: 'usr_last',
    username: 'lin',
    display_name: 'Lin',
    email: null,
    role: 'admin',
    hub_admin: false,
    created_at: '2026-07-21T00:00:00Z',
    identities: [],
    assignable_roles: ['admin'],
    revocable: false,
  },
];

describe('MembersTable', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MembersTable],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  async function mount(inputs: Partial<{ members: readonly MemberView[]; currentUserId: string | null; label: string }> = {}) {
    const fixture = TestBed.createComponent(MembersTable);
    fixture.componentRef.setInput('members', inputs.members ?? MEMBERS);
    fixture.componentRef.setInput('currentUserId', inputs.currentUserId === undefined ? 'usr_admin' : inputs.currentUserId);
    if (inputs.label !== undefined) fixture.componentRef.setInput('label', inputs.label);
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  const row = (el: HTMLElement, id: string) => el.querySelector(`[data-user-id="${id}"]`);

  it('renders one row per member with its username, email, identities, and created date', async () => {
    const { el } = await mount();

    expect(el.querySelectorAll('[data-testid="members-table-row"]')).toHaveLength(3);
    const ada = row(el, 'usr_admin');
    expect(ada?.querySelector('[data-testid="members-table-username"]')?.textContent).toContain('ada');
    expect(ada?.querySelector('[data-testid="members-table-email"]')?.textContent).toContain('ada@example.com');
    expect(ada?.querySelector('[data-testid="members-table-identities"]')?.textContent).toContain('github');
    const when = ada?.querySelector('fleet-when[data-testid="members-table-created-at"]');
    expect(when).toBeTruthy();
    expect(when?.textContent?.trim()).not.toBe(MEMBERS[0].created_at);
  });

  it('labels the panel with its label input', async () => {
    const { el } = await mount({ label: 'Members' });

    expect(el.querySelector('[data-testid="members-table"]')?.getAttribute('aria-label')).toBe('Members');
    expect(el.textContent).toContain('Members');
  });

  it('tags a member who is a hub administrator', async () => {
    const { el } = await mount();

    expect(row(el, 'usr_admin')?.querySelector('[data-testid="members-table-hub-admin"]')).toBeTruthy();
    expect(row(el, 'usr_guest')?.querySelector('[data-testid="members-table-hub-admin"]')).toBeNull();
  });

  it('shows an empty state with no members', async () => {
    const { el } = await mount({ members: [] });

    expect(el.querySelector('[data-testid="members-table-empty"]')).toBeTruthy();
  });

  it("renders the actor's own row as static text with no revoke button", async () => {
    const { el } = await mount();

    const own = row(el, 'usr_admin');
    expect(own?.querySelector('[data-testid="members-table-role-static"]')?.textContent).toContain('admin (you)');
    expect(own?.querySelector('[data-testid="members-table-role-select"]')).toBeNull();
    expect(own?.querySelector('[data-testid="members-table-revoke"]')).toBeNull();
  });

  it('renders a row the hub offers no other role for as static text, without a revoke it does not allow', async () => {
    const { el } = await mount();

    const last = row(el, 'usr_last');
    expect(last?.querySelector('[data-testid="members-table-role-static"]')?.textContent?.trim()).toBe('admin');
    expect(last?.querySelector('[data-testid="members-table-role-select"]')).toBeNull();
    expect(last?.querySelector('[data-testid="members-table-revoke"]')).toBeNull();
  });

  it("offers exactly the hub's assignable roles, in its order", async () => {
    const { fixture, el } = await mount();

    row(el, 'usr_guest')?.querySelector<HTMLButtonElement>('[data-testid="members-table-role-select"]')?.click();
    await fixture.whenStable();
    const options = Array.from(document.querySelectorAll('[role="listbox"] [role="option"]'));
    expect(options.map((o) => o.textContent?.trim())).toEqual(['guest', 'contributor', 'admin']);
    expect(row(el, 'usr_guest')?.querySelector('[data-testid="members-table-role-select"]')?.getAttribute('aria-label')).toBe(
      'Role for grace: guest',
    );
  });

  it('emits changeRole with the member and the chosen role', async () => {
    const { fixture, el } = await mount();
    const emitted: { userId: string; role: string }[] = [];
    fixture.componentInstance.changeRole.subscribe((event) => emitted.push(event));

    row(el, 'usr_guest')?.querySelector<HTMLButtonElement>('[data-testid="members-table-role-select"]')?.click();
    await fixture.whenStable();
    document.querySelector<HTMLElement>('[role="listbox"] [role="option"][title="contributor"]')?.click();
    await fixture.whenStable();

    expect(emitted).toEqual([{ userId: 'usr_guest', role: 'contributor' }]);
  });

  it('emits revoke with the member whose button was pressed', async () => {
    const { fixture, el } = await mount();
    const emitted: MemberView[] = [];
    fixture.componentInstance.revoke.subscribe((member) => emitted.push(member));

    row(el, 'usr_guest')?.querySelector<HTMLElement>('[data-testid="members-table-revoke"]')?.click();
    await fixture.whenStable();

    expect(emitted.map((m) => m.user_id)).toEqual(['usr_guest']);
  });

  it("disables only the controls of a row whose own change is in flight", async () => {
    const pendingRow = { ...MEMBERS[1], user_id: 'usr_other', username: 'otto' };
    const { fixture, el } = await mount({ members: [...MEMBERS, pendingRow] });
    fixture.componentRef.setInput('pendingUserIds', ['usr_guest']);
    await fixture.whenStable();

    const guest = row(el, 'usr_guest');
    expect(guest?.querySelector<HTMLButtonElement>('[data-testid="members-table-role-select"]')?.disabled).toBe(true);
    expect(guest?.querySelector<HTMLButtonElement>('[data-testid="members-table-revoke"]')?.disabled).toBe(true);
    const other = row(el, 'usr_other');
    expect(other?.querySelector<HTMLButtonElement>('[data-testid="members-table-role-select"]')?.disabled).toBe(false);
    expect(other?.querySelector<HTMLButtonElement>('[data-testid="members-table-revoke"]')?.disabled).toBe(false);
  });
});
