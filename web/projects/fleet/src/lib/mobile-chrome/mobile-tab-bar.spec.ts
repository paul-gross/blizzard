import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { MobileTabBar, type MobileTabItem } from './mobile-tab-bar';

async function render(items: readonly MobileTabItem[], testid?: string) {
  await TestBed.configureTestingModule({
    imports: [MobileTabBar],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(MobileTabBar);
  fixture.componentRef.setInput('items', items);
  if (testid !== undefined) fixture.componentRef.setInput('testid', testid);
  await fixture.whenStable();
  return fixture;
}

describe('MobileTabBar', () => {
  it('renders a routed tab as a router-active link', async () => {
    const fixture = await render([{ testid: 'tab-board', label: 'Board', route: '/board' }]);
    const el = fixture.nativeElement as HTMLElement;

    const tab = el.querySelector('[data-testid="tab-board"]');
    expect(tab?.tagName).toBe('A');
    expect(tab?.textContent).toContain('Board');
  });

  it('renders a statically active tab as a highlighted, non-routed button', async () => {
    const fixture = await render([{ testid: 'tab-machine', label: 'Machine', active: true }]);
    const el = fixture.nativeElement as HTMLElement;

    const tab = el.querySelector('[data-testid="tab-machine"]');
    expect(tab?.tagName).toBe('BUTTON');
    expect(tab?.classList.contains('on')).toBe(true);
    expect(tab?.hasAttribute('disabled')).toBe(false);
  });

  it('renders an inert tab dimmed and disabled', async () => {
    const fixture = await render([{ testid: 'tab-transcripts', label: 'Transcripts', inert: true }]);
    const el = fixture.nativeElement as HTMLElement;

    const tab = el.querySelector('[data-testid="tab-transcripts"]');
    expect(tab?.classList.contains('inert')).toBe(true);
    expect(tab?.hasAttribute('disabled')).toBe(true);
  });

  it('omits the badge when falsy', async () => {
    const fixture = await render([
      { testid: 'tab-asks', label: 'Asks', inert: true, badge: 0, badgeTestid: 'tab-asks-badge' },
    ]);
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="tab-asks-badge"]')).toBeNull();
  });

  it('renders a truthy badge under its own testid', async () => {
    const fixture = await render([
      { testid: 'tab-asks', label: 'Asks', inert: true, badge: 2, badgeTestid: 'tab-asks-badge' },
    ]);
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="tab-asks-badge"]')?.textContent).toBe(
      '2',
    );
  });

  it('roots its own data-testid on the nav element, defaulting to mobile-tab-bar', async () => {
    const fixture = await render([]);
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="mobile-tab-bar"]')).not.toBeNull();
  });

  it('takes a custom root testid so two mounts never collide', async () => {
    const fixture = await render([], 'runner-mobile-tab-bar');
    expect(
      (fixture.nativeElement as HTMLElement).querySelector('[data-testid="runner-mobile-tab-bar"]'),
    ).not.toBeNull();
  });
});
