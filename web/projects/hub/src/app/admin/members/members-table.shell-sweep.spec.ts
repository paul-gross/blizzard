import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page, userEvent } from 'vitest/browser';

import { member } from './members.builders';
import type { Member } from './members.domain';
import { MembersTable } from './members-table';

/**
 * The members table's real-render proof (the tooled half of `blizzard-context:/verification/blizzard.md`'s
 * `web:shell-sweep` method): at every width, with a tenant name and member details of any length, the page never
 * scrolls sideways — a table wider than a phone scrolls inside its own panel instead, and the revoke button stays
 * reachable there.
 * A real CSS layout claim jsdom cannot make.
 *
 * It also drives the table's keyboard model with real keys: the grid is one Tab stop; the arrow keys reach the role
 * select and the revoke button and skip a pending row's disabled controls and every empty revoke cell; choosing a role
 * by keyboard emits `changeRole` once, and Enter or Space on revoke emits `revoke` once.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it needs
 * `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
/** A name with no break opportunity, so the table cannot wrap its way out of the width. */
const LONG = 'averylongnamethatapersonoratenantmightreallycarryandthatmustnotpushthepagesideways';
const MEMBERS: readonly Member[] = [
  member({
    userId: 'usr_admin',
    username: 'ada',
    displayName: 'Ada',
    email: 'ada@example.com',
    role: 'admin',
    hubAdmin: true,
    createdAt: '2026-07-21T00:00:00Z',
    identities: [{ providerName: 'github', handle: 'ada' }],
    assignableRoles: [],
    revocable: false,
  }),
  member({
    userId: 'usr_long',
    username: LONG,
    displayName: LONG,
    email: `${LONG}@example.com`,
    role: { unlisted: LONG },
    hubAdmin: true,
    createdAt: '2026-07-21T00:00:00Z',
    identities: [{ providerName: 'github', handle: LONG }],
    assignableRoles: ['guest', 'contributor', 'admin'],
    revocable: true,
  }),
];

async function sweep(width: number) {
  const pageErrors: string[] = [];
  const onError = (e: ErrorEvent) => pageErrors.push(e.message);
  window.addEventListener('error', onError);

  await TestBed.configureTestingModule({
    imports: [MembersTable],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(MembersTable);
  fixture.componentRef.setInput('members', MEMBERS);
  fixture.componentRef.setInput('state', 'loading');
  fixture.componentRef.setInput('label', `Members ${LONG}`);
  fixture.componentRef.setInput('currentUserId', 'usr_admin');
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await fixture.whenStable();
  expect(root.querySelector('[data-testid="members-page-loading"]')).not.toBeNull();
  fixture.componentRef.setInput('state', 'ready');
  await fixture.whenStable();
  try {
    await page.viewport(width, 900);
    await new Promise((resolve) => requestAnimationFrame(resolve));

    expect(
      document.documentElement.scrollWidth,
      `the page scrolls sideways at ${width}px (${document.documentElement.scrollWidth} > ${width})`,
    ).toBeLessThanOrEqual(width);
    const panel = root.querySelector<HTMLElement>('[data-testid="members-table"]')!;
    expect(panel.getBoundingClientRect().right, `the panel overruns ${width}px`).toBeLessThanOrEqual(width);

    const scroller = root.querySelector<HTMLElement>('[data-testid="members-table-scroll"]')!;
    expect(scroller.scrollWidth, 'the table must overflow its scroll container').toBeGreaterThan(scroller.clientWidth);
    scroller.scrollLeft = scroller.scrollWidth;
    expect(scroller.scrollLeft, 'the table cannot scroll horizontally').toBeGreaterThan(0);
    scroller.scrollLeft = 0;
    expect(scroller.scrollLeft, 'the table cannot scroll back').toBe(0);

    for (const testid of ['members-table-role-select', 'members-table-revoke']) {
      const control = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!;
      control.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const box = control.getBoundingClientRect();
      const viewport = scroller.getBoundingClientRect();
      expect(box.width, `${testid} has no width`).toBeGreaterThan(0);
      expect(box.left, `${testid} is clipped on the left at ${width}px`).toBeGreaterThanOrEqual(viewport.left);
      expect(box.right, `${testid} is clipped on the right at ${width}px`).toBeLessThanOrEqual(viewport.right);
      expect(box.left, `${testid} is outside the screen at ${width}px`).toBeGreaterThanOrEqual(0);
      expect(box.right, `${testid} is outside the screen at ${width}px`).toBeLessThanOrEqual(width);
    }
    const select = root.querySelector<HTMLButtonElement>('[data-testid="members-table-role-select"]')!;
    expect(select.getAttribute('aria-label')).toContain(LONG);
    select.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    if (width <= 390) {
      scroller.scrollLeft += select.getBoundingClientRect().left - (width - select.getBoundingClientRect().width) / 2;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      expect(Math.abs(select.getBoundingClientRect().left - (width - select.getBoundingClientRect().width) / 2)).toBeLessThan(1);
    }
    select.click();
    await fixture.whenStable();
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const popup = document.querySelector<HTMLElement>('[role="listbox"]')!;
    const option = [...popup.querySelectorAll<HTMLElement>('[role="option"]')].find((item) => item.textContent?.trim() === LONG)!;
    const bounds = option.getBoundingClientRect();
    const popupBounds = popup.getBoundingClientRect();
    expect(bounds.left).toBeGreaterThanOrEqual(0);
    expect(bounds.right).toBeLessThanOrEqual(width);
    expect(bounds.top).toBeGreaterThanOrEqual(popupBounds.top);
    expect(bounds.bottom).toBeLessThanOrEqual(popupBounds.bottom);
    expect(popupBounds.left).toBeGreaterThanOrEqual(0);
    expect(popupBounds.right).toBeLessThanOrEqual(width);
    expect(option.scrollWidth).toBeLessThanOrEqual(option.clientWidth);
    expect(option.scrollHeight).toBeLessThanOrEqual(option.clientHeight);
    expect(bounds.height).toBeGreaterThan(30);
    select.click();
  } finally {
    root.remove();
    window.removeEventListener('error', onError);
  }
  expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
}

describe('members table shell sweep (web:shell-sweep)', () => {
  for (const width of [320, 360, 390, 768, 1280]) {
    it(`never scrolls the page sideways at ${width}px`, async () => {
      await sweep(width);
    });
  }
});

const SELF = MEMBERS[0];
const ASSIGNABLE: Member = { ...MEMBERS[1], userId: 'usr_grace', username: 'grace', displayName: 'Grace', email: null, identities: [], role: 'guest' };
const PENDING: Member = { ...ASSIGNABLE, userId: 'usr_otto', username: 'otto', displayName: 'Otto' };

describe('members table keyboard sweep (web:shell-sweep)', () => {
  const settle = () => new Promise<void>((done) => setTimeout(done, 80));
  const press = async (keys: string) => {
    await userEvent.keyboard(keys);
    await settle();
  };

  async function mount() {
    await TestBed.configureTestingModule({
      imports: [MembersTable],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const before = document.createElement('button');
    const after = document.createElement('button');
    before.textContent = 'before';
    after.textContent = 'after';
    document.body.append(before);
    const fixture = TestBed.createComponent(MembersTable);
    fixture.componentRef.setInput('members', [SELF, ASSIGNABLE, PENDING]);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('currentUserId', SELF.userId);
    fixture.componentRef.setInput('pendingUserIds', [PENDING.userId]);
    const root = fixture.nativeElement as HTMLElement;
    document.body.append(root, after);
    await page.viewport(1280, 900);
    await fixture.whenStable();
    const changes: { userId: string; role: string }[] = [];
    const revokes: string[] = [];
    fixture.componentInstance.changeRole.subscribe((e) => changes.push(e));
    fixture.componentInstance.revoke.subscribe((m) => revokes.push(m.userId));
    const cleanup = () => {
      fixture.destroy();
      root.remove();
      before.remove();
      after.remove();
    };
    return { fixture, root, before, after, changes, revokes, cleanup };
  }

  const control = (root: HTMLElement, testid: string, userId: string) =>
    root.querySelector<HTMLElement>(`[data-user-id="${userId}"] [data-testid="${testid}"]`)!;

  async function run(body: (ctx: Awaited<ReturnType<typeof mount>>) => Promise<void>) {
    const ctx = await mount();
    try {
      await body(ctx);
    } finally {
      ctx.cleanup();
    }
  }

  it('is one Tab stop', () =>
    run(async ({ root, before, after }) => {
      before.focus();
      await press('{Tab}');
      expect(root.querySelector('table')!.contains(document.activeElement)).toBe(true);
      await press('{Tab}');
      expect(document.activeElement).toBe(after);
    }));

  it('reaches the role select and revoke button by arrow keys, and skips disabled controls and empty revoke cells', () =>
    run(async ({ root, before }) => {
      before.focus();
      await press('{Tab}');
      // Down to the assignable row, then right through username, display name, email, identities, to the role.
      await press('{ArrowDown}{ArrowRight}{ArrowRight}{ArrowRight}{ArrowRight}');
      const select = control(root, 'members-table-role-select', 'usr_grace');
      expect(document.activeElement).toBe(select);
      await press('{ArrowRight}{ArrowRight}');
      expect(document.activeElement).toBe(control(root, 'members-table-revoke', 'usr_grace'));

      // The pending row below holds disabled controls: Down does not enter it.
      await press('{ArrowDown}');
      expect(document.activeElement).toBe(control(root, 'members-table-revoke', 'usr_grace'));
      // Down from the role column passes over the pending row's disabled select, onto the self row's static role text.
      await press('{ArrowLeft}{ArrowLeft}{ArrowDown}');
      expect(document.activeElement).toBe(root.querySelector('[data-user-id="usr_admin"] [data-testid="members-table-role-static"]')!.closest('td'));
      // Static role text stays navigable; the self row's empty revoke cell does not.
      await press('{ArrowRight}{ArrowRight}');
      expect(document.activeElement?.closest('td')?.matches('.actions')).toBe(false);
    }));

  it('chooses a role by keyboard and emits changeRole once', () =>
    run(async ({ root, before, changes }) => {
      before.focus();
      await press('{Tab}{ArrowDown}{ArrowRight}{ArrowRight}{ArrowRight}{ArrowRight}');
      const select = control(root, 'members-table-role-select', 'usr_grace');
      await press(' ');
      expect(document.querySelectorAll('[role="listbox"]')).toHaveLength(1);
      await press('{ArrowDown}');
      await press('{Enter}');
      expect(changes).toEqual([{ userId: 'usr_grace', role: 'contributor' }]);
      expect(document.querySelector('[role="listbox"]')).toBeNull();
      expect(document.activeElement).toBe(select);
    }));

  it('keeps keyboard focus on the role select through a pick whose row goes pending and back', () =>
    run(async ({ fixture, root, before }) => {
      before.focus();
      await press('{Tab}{ArrowDown}{ArrowRight}{ArrowRight}{ArrowRight}{ArrowRight}');
      const select = control(root, 'members-table-role-select', 'usr_grace');
      fixture.componentInstance.changeRole.subscribe(() =>
        fixture.componentRef.setInput('pendingUserIds', ['usr_otto', 'usr_grace']),
      );
      await press(' ');
      await press('{ArrowDown}');
      await press('{Enter}');
      expect((select as HTMLButtonElement).disabled).toBe(true);
      fixture.componentRef.setInput('pendingUserIds', ['usr_otto']);
      await settle();
      expect(document.activeElement).toBe(select);
      expect(select.getAttribute('tabindex')).toBe('0');
      await press('{ArrowRight}{ArrowRight}');
      expect(document.activeElement).toBe(control(root, 'members-table-revoke', 'usr_grace'));
    }));

  for (const [name, key] of [['Enter', '{Enter}'], ['Space', ' ']] as const) {
    it(`${name} on revoke emits revoke once`, () =>
      run(async ({ root, before, revokes }) => {
        before.focus();
        await press('{Tab}{ArrowDown}{ArrowRight}{ArrowRight}{ArrowRight}{ArrowRight}{ArrowRight}{ArrowRight}');
        expect(document.activeElement).toBe(control(root, 'members-table-revoke', 'usr_grace'));
        await press(key);
        expect(revokes).toEqual(['usr_grace']);
      }));
  }
});
