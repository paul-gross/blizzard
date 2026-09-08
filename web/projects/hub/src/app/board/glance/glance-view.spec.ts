import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { KitAsyncStateValue } from 'fleet';

import { GlanceView, type Vitals } from './glance-view';

const VITALS: Vitals = {
  needsYou: 0,
  running: 0,
  runnersUpLabel: '0/0',
  live: false,
  liveLabel: 'connecting',
};

describe('GlanceView', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GlanceView],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  /** Render with every state defaulting to `'ready'` (empty rows) unless overridden —
 * each panel's own state is what this spec exercises independently. */
  function render(
    overrides: Partial<Record<'needsYouState' | 'inMotionState' | 'upNextState' | 'doneTodayState' | 'spendState', KitAsyncStateValue>> = {},
  ) {
    const fixture = TestBed.createComponent(GlanceView);
    fixture.componentRef.setInput('vitals', VITALS);
    fixture.componentRef.setInput('needsYouState', overrides.needsYouState ?? 'empty');
    fixture.componentRef.setInput('inMotionState', overrides.inMotionState ?? 'empty');
    fixture.componentRef.setInput('upNextState', overrides.upNextState ?? 'empty');
    fixture.componentRef.setInput('doneTodayState', overrides.doneTodayState ?? 'empty');
    fixture.componentRef.setInput('spendState', overrides.spendState ?? 'ready');
    return fixture;
  }

  it('withholds "Needs you"\'s empty copy while its own state is loading, independently of the other four (AC 4)', async () => {
    const fixture = render({ needsYouState: 'loading' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="needs-you-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="needs-you-empty"]')).toBeNull();
    // The other three are unaffected — still resolved to their own empty states.
    expect(el.querySelector('[data-testid="in-motion-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="up-next-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="done-today-empty"]')).not.toBeNull();
  });

  it('withholds "In motion"\'s empty copy while its own state is loading, independently of the other three', async () => {
    const fixture = render({ inMotionState: 'loading' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="in-motion-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="in-motion-empty"]')).toBeNull();
    expect(el.querySelector('[data-testid="needs-you-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="up-next-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="done-today-empty"]')).not.toBeNull();
  });

  it('withholds "Up next"\'s empty copy while its own state is loading, independently of the other panels', async () => {
    const fixture = render({ upNextState: 'loading' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="up-next-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="up-next-empty"]')).toBeNull();
    expect(el.querySelector('[data-testid="needs-you-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="in-motion-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="done-today-empty"]')).not.toBeNull();
  });

  it('withholds "Done today"\'s empty copy while its own state is loading, independently of the other three', async () => {
    const fixture = render({ doneTodayState: 'loading' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="done-today-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="done-today-empty"]')).toBeNull();
    expect(el.querySelector('[data-testid="needs-you-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="in-motion-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="up-next-empty"]')).not.toBeNull();
  });

  it('shows the spend panel\'s own loading placeholder distinctly from its error state', async () => {
    const fixture = render({ spendState: 'loading' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="glance-spend-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="glance-spend-row"]')).toBeNull();

    fixture.componentRef.setInput('spendState', 'error');
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="glance-spend-error"]')).not.toBeNull();
  });

  it('shows each panel\'s error state distinctly from empty', async () => {
    const fixture = render({ needsYouState: 'error', inMotionState: 'error', upNextState: 'error', doneTodayState: 'error' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="needs-you-error"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="in-motion-error"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="up-next-error"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="done-today-error"]')).not.toBeNull();
  });

  it('renders every panel\'s empty copy once all four states have settled with nothing to show', async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="needs-you-empty"]')?.textContent).toContain('NOTHING NEEDS YOU');
    expect(el.querySelector('[data-testid="in-motion-empty"]')?.textContent).toContain('NOTHING IN MOTION');
    expect(el.querySelector('[data-testid="up-next-empty"]')?.textContent).toContain('NOTHING UP NEXT');
    expect(el.querySelector('[data-testid="done-today-empty"]')?.textContent).toContain('NOTHING DONE YET');
  });

  it('renders Done today\'s visible/total terminal count even at zero', async () => {
    const fixture = render();
    await fixture.whenStable();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="done-today-count"]')?.textContent).toContain('0/0');
  });

  it('hides Done today\'s count while the chunks-derived panel is loading', async () => {
    const fixture = render({ doneTodayState: 'loading' });
    fixture.componentRef.setInput('doneTodayTotal', null);
    await fixture.whenStable();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="done-today-count"]')).toBeNull();
  });

  it('hides Done today\'s count when the chunks-derived panel errors', async () => {
    const fixture = render({ doneTodayState: 'error' });
    fixture.componentRef.setInput('doneTodayTotal', null);
    await fixture.whenStable();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="done-today-count"]')).toBeNull();
  });
});
