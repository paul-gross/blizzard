import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { MobileTitlebar } from './mobile-titlebar';

describe('MobileTitlebar', () => {
  beforeEach(async () => {
    localStorage.clear();
    await TestBed.configureTestingModule({
      imports: [MobileTitlebar],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the brand and no board/graphs nav — navigation lives in the bottom tab bar', async () => {
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-titlebar"]')?.textContent).toContain('blizzard');
    expect(el.querySelector('[data-testid="nav-board"]')).toBeNull();
    expect(el.querySelector('[data-testid="nav-graphs"]')).toBeNull();
  });

  it('reflects the live input on the live dot', async () => {
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', false);
    await fixture.whenStable();
    let el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="mobile-titlebar-livedot"]')?.classList.contains('active')).toBe(false);

    fixture.componentRef.setInput('live', true);
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="mobile-titlebar-livedot"]')?.classList.contains('active')).toBe(true);
  });

  it('buries the viewport toggle behind the overflow menu, closed by default', async () => {
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('fleet-viewport-toggle')).toBeNull();

    el.querySelector<HTMLElement>('[data-testid="mobile-titlebar-menu"]')?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="mobile-titlebar-menu-panel"] fleet-viewport-toggle')).not.toBeNull();
  });
});
