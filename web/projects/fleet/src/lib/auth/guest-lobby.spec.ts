import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import type { MeResponse } from '../api/hub';
import { GuestLobby } from './guest-lobby';

const GUEST: MeResponse = {
  user_id: 'usr_1',
  username: 'newcomer',
  display_name: 'Newcomer',
  role: 'guest',
  permissions: [],
};

describe('GuestLobby', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GuestLobby],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the awaiting-access state with the resolved identity', async () => {
    const fixture = TestBed.createComponent(GuestLobby);
    fixture.componentRef.setInput('me', GUEST);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="guest-lobby"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="guest-lobby-username"]')?.textContent).toContain('newcomer');
  });

  it('emits logout when the control is clicked', async () => {
    const fixture = TestBed.createComponent(GuestLobby);
    fixture.componentRef.setInput('me', GUEST);
    const logout = vi.fn();
    fixture.componentInstance.logout.subscribe(logout);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="guest-lobby-logout"]')?.click();

    expect(logout).toHaveBeenCalledTimes(1);
  });
});
