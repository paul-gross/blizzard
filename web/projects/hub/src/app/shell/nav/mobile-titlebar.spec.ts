import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { MobileTitlebar } from './mobile-titlebar';

/** The CDK renders each menu into an overlay on `document.body`, outside the
 * fixture's own element. */
const inOverlay = (selector: string) => document.body.querySelector<HTMLElement>(selector);

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

  it('buries the appearance switcher behind the overflow menu, closed by default', async () => {
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(inOverlay('[data-testid="mobile-titlebar-appearance"]')).toBeNull();

    el.querySelector<HTMLElement>('[data-testid="mobile-titlebar-menu"]')?.click();
    await fixture.whenStable();

    expect(
      inOverlay('[data-testid="mobile-titlebar-menu-panel"] [data-testid="mobile-titlebar-appearance"]'),
    ).not.toBeNull();

    inOverlay('[data-testid="mobile-titlebar-appearance"]')?.click();
    await fixture.whenStable();

    expect(
      inOverlay('[data-testid="mobile-titlebar-appearance-panel"] [data-testid="viewport-menu-auto"]'),
    ).not.toBeNull();
  });
});
