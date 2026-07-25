import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { AppNavMenu } from './app-nav-menu';

describe('AppNavMenu', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AppNavMenu],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the avatar trigger, closed by default', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="app-nav-menu"] fleet-kit-avatar')).not.toBeNull();
    expect(el.querySelector('[data-testid="nav-logout"]')).toBeNull();
    expect(el.querySelector('fleet-viewport-toggle')).toBeNull();
  });

  it('reveals Log out and the viewport toggle once opened', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="app-nav-menu-panel"] [data-testid="nav-logout"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="app-nav-menu-panel"] fleet-viewport-toggle')).not.toBeNull();
  });

  it('emits logout when the Log out entry is clicked', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    const logout = vi.fn();
    fixture.componentInstance.logout.subscribe(logout);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLElement>('[data-testid="nav-logout"]')?.click();

    expect(logout).toHaveBeenCalledTimes(1);
  });
});
