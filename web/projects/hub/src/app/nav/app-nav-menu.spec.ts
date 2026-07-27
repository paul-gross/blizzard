import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { AppNavMenu } from './app-nav-menu';

/** The CDK renders each menu into an overlay on `document.body`, outside the
 * fixture's own element. */
const inOverlay = (selector: string) => document.body.querySelector<HTMLElement>(selector);

describe('AppNavMenu', () => {
  beforeEach(async () => {
    localStorage.clear();
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
    expect(inOverlay('[data-testid="nav-logout"]')).toBeNull();
    expect(inOverlay('[data-testid="nav-appearance"]')).toBeNull();
  });

  it('reveals Log out and the Appearance submenu item once opened', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await fixture.whenStable();

    expect(inOverlay('[data-testid="app-nav-menu-panel"] [data-testid="nav-logout"]')).not.toBeNull();
    expect(inOverlay('[data-testid="app-nav-menu-panel"] [data-testid="nav-appearance"]')).not.toBeNull();
    // The switcher is a real submenu now (issue #161), not an inline chip row —
    // so it stays closed until its own item is entered.
    expect(inOverlay('[data-testid="nav-appearance-panel"]')).toBeNull();
  });

  it('opens the appearance switcher as a submenu of the profile menu (issue #161)', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await fixture.whenStable();
    inOverlay('[data-testid="nav-appearance"]')?.click();
    await fixture.whenStable();

    expect(inOverlay('[data-testid="nav-appearance-panel"] [data-testid="viewport-menu-auto"]')).not.toBeNull();
    expect(inOverlay('[data-testid="nav-appearance-panel"] [data-testid="viewport-menu-mobile"]')).not.toBeNull();
    expect(inOverlay('[data-testid="nav-appearance-panel"] [data-testid="viewport-menu-desktop"]')).not.toBeNull();
    // The parent menu stays open behind it — a submenu, not a replacement.
    expect(inOverlay('[data-testid="app-nav-menu-panel"]')).not.toBeNull();
  });

  it('emits logout when the Log out entry is triggered', async () => {
    const fixture = TestBed.createComponent(AppNavMenu);
    const logout = vi.fn();
    fixture.componentInstance.logout.subscribe(logout);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await fixture.whenStable();
    inOverlay('[data-testid="nav-logout"]')?.click();

    expect(logout).toHaveBeenCalledTimes(1);
  });
});
