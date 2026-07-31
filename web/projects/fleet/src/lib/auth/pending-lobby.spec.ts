import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import type { MeResponse } from '../api/hub';
import { PendingLobby } from './pending-lobby';

const PENDING: MeResponse = {
  user_id: 'usr_1',
  username: 'newcomer',
  display_name: 'Newcomer',
  role: 'pending',
  permissions: [],
};

describe('PendingLobby', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PendingLobby],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the awaiting-access state with the resolved identity', async () => {
    const fixture = TestBed.createComponent(PendingLobby);
    fixture.componentRef.setInput('me', PENDING);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="pending-lobby"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="pending-lobby-username"]')?.textContent).toContain('newcomer');
  });

  it('emits logout when the control is clicked', async () => {
    const fixture = TestBed.createComponent(PendingLobby);
    fixture.componentRef.setInput('me', PENDING);
    const logout = vi.fn();
    fixture.componentInstance.logout.subscribe(logout);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="pending-lobby-logout"]')?.click();

    expect(logout).toHaveBeenCalledTimes(1);
  });
});
