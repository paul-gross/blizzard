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

  it('renders the brand mark and wordmark under its default testid', async () => {
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-titlebar"]')?.textContent).toContain('blizzard');
    expect(el.querySelector('fleet-brand-mark')).not.toBeNull();
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

  it('buries the overflow menu panel, closed by default', async () => {
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-titlebar-menu-panel"]')).toBeNull();

    el.querySelector<HTMLElement>('[data-testid="mobile-titlebar-menu"]')?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="mobile-titlebar-menu-panel"]')).not.toBeNull();
  });

  it('derives every handle from a custom testid, so two mounts never collide', async () => {
    const fixture = TestBed.createComponent(MobileTitlebar);
    fixture.componentRef.setInput('live', true);
    fixture.componentRef.setInput('testid', 'runner-mobile-titlebar');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="runner-mobile-titlebar"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="runner-mobile-titlebar-livedot"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="runner-mobile-titlebar-menu"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="mobile-titlebar"]')).toBeNull();
  });
});
