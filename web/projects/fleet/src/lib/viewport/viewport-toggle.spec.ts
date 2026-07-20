import { BreakpointObserver, type BreakpointState } from '@angular/cdk/layout';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';

import { ViewportToggle } from './viewport-toggle';

const STORAGE_KEY = 'blizzard.viewport.override';

class FakeBreakpointObserver {
  matches = false;
  readonly changes = new Subject<BreakpointState>();

  isMatched(): boolean {
    return this.matches;
  }

  observe(): Subject<BreakpointState> {
    return this.changes;
  }
}

describe('ViewportToggle', () => {
  let breakpoint: FakeBreakpointObserver;

  beforeEach(async () => {
    localStorage.clear();
    breakpoint = new FakeBreakpointObserver();
    await TestBed.configureTestingModule({
      imports: [ViewportToggle],
      providers: [provideZonelessChangeDetection(), { provide: BreakpointObserver, useValue: breakpoint }],
    }).compileComponents();
  });

  it('renders a chip per override option and reflects the effective mode', async () => {
    const fixture = TestBed.createComponent(ViewportToggle);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="viewport-toggle-auto"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="viewport-toggle-mobile"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="viewport-toggle-desktop"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="viewport-toggle-mode"]')?.textContent?.trim()).toBe('desktop');
  });

  it('follows the breakpoint while on auto, then reflects a click override', async () => {
    const fixture = TestBed.createComponent(ViewportToggle);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    breakpoint.changes.next({ matches: true, breakpoints: {} });
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="viewport-toggle-mode"]')?.textContent?.trim()).toBe('mobile');

    (el.querySelector('[data-testid="viewport-toggle-desktop"]') as HTMLButtonElement).click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="viewport-toggle-mode"]')?.textContent?.trim()).toBe('desktop');
    expect(el.querySelector('[data-testid="viewport-toggle-desktop"]')?.classList.contains('selected')).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe('desktop');
  });

  it('clicking auto returns to the breakpoint-derived mode', async () => {
    const fixture = TestBed.createComponent(ViewportToggle);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    (el.querySelector('[data-testid="viewport-toggle-mobile"]') as HTMLButtonElement).click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="viewport-toggle-mode"]')?.textContent?.trim()).toBe('mobile');

    (el.querySelector('[data-testid="viewport-toggle-auto"]') as HTMLButtonElement).click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="viewport-toggle-mode"]')?.textContent?.trim()).toBe('desktop');
    expect(el.querySelector('[data-testid="viewport-toggle-auto"]')?.classList.contains('selected')).toBe(true);
  });
});
