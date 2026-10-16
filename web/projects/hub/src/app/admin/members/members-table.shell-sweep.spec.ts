import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { MemberView } from 'fleet';
import { MembersTable } from './members-table';

/**
 * The members table's real-render proof (the tooled half of `blizzard-context:/verification/blizzard.md`'s
 * `web:shell-sweep` method): at every width, with a tenant name and member details of any length, the page never
 * scrolls sideways — a table wider than a phone scrolls inside its own panel instead, and the revoke button stays
 * reachable there.
 * A real CSS layout claim jsdom cannot make.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it needs
 * `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
/** A name with no break opportunity, so the table cannot wrap its way out of the width. */
const LONG = 'averylongnamethatapersonoratenantmightreallycarryandthatmustnotpushthepagesideways';
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
    user_id: 'usr_long',
    username: LONG,
    display_name: LONG,
    email: `${LONG}@example.com`,
    role: 'guest',
    hub_admin: true,
    created_at: '2026-07-21T00:00:00Z',
    identities: [{ provider_name: 'github', handle: LONG }],
    assignable_roles: ['guest', 'contributor', 'admin'],
    revocable: true,
  },
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
  fixture.componentRef.setInput('label', `Members ${LONG}`);
  fixture.componentRef.setInput('currentUserId', 'usr_admin');
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
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

    const table = root.querySelector('table')!;
    if (width === 360) {
      expect(table.scrollWidth, 'the table fits a phone, so this width proves nothing').toBeGreaterThan(width);
    }

    const revoke = root.querySelector<HTMLElement>('[data-testid="members-table-revoke"]')!;
    revoke.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const box = revoke.getBoundingClientRect();
    expect(box.width, 'the revoke button has no width').toBeGreaterThan(0);
    expect(box.right, `the revoke button cannot be scrolled into view at ${width}px`).toBeLessThanOrEqual(width);
  } finally {
    root.remove();
    window.removeEventListener('error', onError);
  }
  expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
}

describe('members table shell sweep (web:shell-sweep)', () => {
  for (const width of [360, 768, 1280]) {
    it(`never scrolls the page sideways at ${width}px`, async () => {
      await sweep(width);
    });
  }
});
