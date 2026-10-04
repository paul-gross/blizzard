import { BreakpointObserver, type BreakpointState } from '@angular/cdk/layout';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';

import { ViewportMenu } from './viewport-menu';

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

describe('ViewportMenu', () => {
  let breakpoint: FakeBreakpointObserver;
  let fixture: ReturnType<typeof TestBed.createComponent<ViewportMenu>>;
  let el: HTMLElement;

  const option = (value: string) => el.querySelector<HTMLElement>(`[data-testid="viewport-menu-${value}"]`);
  const mode = () => el.querySelector('[data-testid="viewport-menu-mode"]')?.textContent?.trim();

  beforeEach(async () => {
    localStorage.clear();
    breakpoint = new FakeBreakpointObserver();
    await TestBed.configureTestingModule({
      imports: [ViewportMenu],
      providers: [provideZonelessChangeDetection(), { provide: BreakpointObserver, useValue: breakpoint }],
    }).compileComponents();
    fixture = TestBed.createComponent(ViewportMenu);
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
  });

  it('renders the three overrides as a radio group and reflects the effective mode', () => {
    expect(el.querySelector('[role="menu"]')).not.toBeNull();
    expect(option('auto')?.getAttribute('role')).toBe('menuitemradio');
    expect(option('mobile')?.getAttribute('role')).toBe('menuitemradio');
    expect(option('desktop')?.getAttribute('role')).toBe('menuitemradio');
    expect(option('auto')?.getAttribute('aria-checked')).toBe('true');
    expect(mode()).toBe('desktop');
  });

  it('follows the breakpoint while on auto, then reflects a chosen override', async () => {
    breakpoint.changes.next({ matches: true, breakpoints: {} });
    await fixture.whenStable();
    expect(mode()).toBe('mobile');

    option('desktop')?.click();
    await fixture.whenStable();

    expect(mode()).toBe('desktop');
    expect(option('desktop')?.getAttribute('aria-checked')).toBe('true');
    expect(option('auto')?.getAttribute('aria-checked')).toBe('false');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('desktop');
  });

  it('choosing auto returns to the breakpoint-derived mode', async () => {
    option('mobile')?.click();
    await fixture.whenStable();
    expect(mode()).toBe('mobile');

    option('auto')?.click();
    await fixture.whenStable();

    expect(mode()).toBe('desktop');
    expect(option('auto')?.getAttribute('aria-checked')).toBe('true');
  });
});
