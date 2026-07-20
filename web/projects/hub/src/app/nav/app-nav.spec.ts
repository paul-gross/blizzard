import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AppNav } from './app-nav';

describe('AppNav', () => {
  beforeEach(async () => {
    localStorage.clear();
    await TestBed.configureTestingModule({
      imports: [AppNav],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  it('renders the board/graphs route tabs', async () => {
    const fixture = TestBed.createComponent(AppNav);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="nav-board"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="nav-graphs"]')).not.toBeNull();
  });

  it('buries the viewport toggle behind the overflow menu, closed by default', async () => {
    const fixture = TestBed.createComponent(AppNav);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="app-nav-menu"]')).not.toBeNull();
    expect(el.querySelector('fleet-viewport-toggle')).toBeNull();

    el.querySelector<HTMLElement>('[data-testid="app-nav-menu"]')?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="app-nav-menu-panel"] fleet-viewport-toggle')).not.toBeNull();
  });
});
