import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import type { MeResponse } from 'fleet';
import { NoAccess } from './no-access';

const MEMBERLESS: MeResponse = {
  user_id: 'usr_1',
  username: 'newcomer',
  display_name: 'Newcomer',
  role: null,
  permissions: [],
  tenants: [],
  hub_admin: false,
};

describe('NoAccess', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [NoAccess],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('tells the person to reach out to their administrator, naming who is signed in', async () => {
    const fixture = TestBed.createComponent(NoAccess);
    fixture.componentRef.setInput('me', MEMBERLESS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="no-access"]')?.textContent).toContain(
      "You don't have access to anything here yet.",
    );
    expect(el.querySelector('[data-testid="no-access"]')?.textContent).toContain(
      'Reach out to your administrator for access.',
    );
    expect(el.querySelector('[data-testid="no-access-username"]')?.textContent).toContain('newcomer');
  });

  it('emits logout when the control is clicked', async () => {
    const fixture = TestBed.createComponent(NoAccess);
    fixture.componentRef.setInput('me', MEMBERLESS);
    const logout = vi.fn();
    fixture.componentInstance.logout.subscribe(logout);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="no-access-logout"]')?.click();

    expect(logout).toHaveBeenCalledTimes(1);
  });

  it('disables the control while the logout is in flight and renders its failure', async () => {
    const fixture = TestBed.createComponent(NoAccess);
    fixture.componentRef.setInput('me', MEMBERLESS);
    fixture.componentRef.setInput('logoutPending', true);
    fixture.componentRef.setInput('logoutError', 'session store down');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="no-access-logout"]')?.disabled).toBe(true);
    expect(el.querySelector('[data-testid="no-access-logout-error"]')?.textContent).toContain('session store down');
  });
});
