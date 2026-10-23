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

  it("renders the in-motion row's and the spend panel's own entirely-estimated figure as the estimate alone, marked ~", async () => {
    const fixture = render({ inMotionState: 'ready' });
    fixture.componentRef.setInput('inMotion', [
      {
        chunkId: 'ch_01estimate0000000000000000',
        shortId: 'C-0001',
        node: 'build',
        status: null,
        lines: [],
        itemsRead: true,
        costUsd: 0,
        costPartial: false,
        estimatedCostUsd: 0.07,
      },
    ]);
    fixture.componentRef.setInput('spend', {
      since: '2026-07-20T00:00:00Z',
      input_tokens: 900,
      output_tokens: 400,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
      cost_usd: 0,
      cost_partial: false,
      estimated_cost_usd: 0.07,
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="in-motion-cost"]')?.textContent?.trim()).toBe('~$0.07');
    expect(el.querySelector('[data-testid="glance-spend-value"]')?.textContent?.trim()).toBe('~$0.07');
  });

  it('folds a billed cost and its own estimate into one in-motion figure, marked ~', async () => {
    const fixture = render({ inMotionState: 'ready' });
    fixture.componentRef.setInput('inMotion', [
      {
        chunkId: 'ch_01billedandestimate00000000',
        shortId: 'C-0003',
        node: 'build',
        status: null,
        lines: [],
        itemsRead: true,
        costUsd: 0.05,
        costPartial: false,
        estimatedCostUsd: 2,
      },
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="in-motion-cost"]')?.textContent?.trim()).toBe('~$2.05');
  });

  it('renders no ~ marker for a chunk/spend read with no estimate — exactly as before this field existed', async () => {
    const fixture = render({ inMotionState: 'ready' });
    fixture.componentRef.setInput('inMotion', [
      {
        chunkId: 'ch_01billedonly000000000000000',
        shortId: 'C-0002',
        node: 'build',
        status: null,
        lines: [],
        itemsRead: true,
        costUsd: 2.03,
        costPartial: false,
        estimatedCostUsd: null,
      },
    ]);
    fixture.componentRef.setInput('spend', {
      since: '2026-07-20T00:00:00Z',
      input_tokens: 900,
      output_tokens: 400,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
      cost_usd: 18.4,
      cost_partial: false,
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="in-motion-cost"]')?.textContent?.trim()).toBe('$2.03');
    expect(el.querySelector('[data-testid="glance-spend-row"]')?.textContent).toContain('$18.40');
  });

  it('renders no cost figure at all on an in-motion row that has spent nothing and carries no estimate', async () => {
    const fixture = render({ inMotionState: 'ready' });
    fixture.componentRef.setInput('inMotion', [
      {
        chunkId: 'ch_01nothingspent0000000000000',
        shortId: 'C-0004',
        node: 'build',
        status: null,
        lines: [],
        itemsRead: true,
        costUsd: 0,
        costPartial: false,
        estimatedCostUsd: null,
      },
    ]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="in-motion-row"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="in-motion-cost"]')).toBeNull();
  });

  it("leads a row with its work item's label and title, and names only the status its panel does not", async () => {
    const fixture = render({ inMotionState: 'ready' });
    fixture.componentRef.setInput('inMotion', [
      {
        chunkId: 'ch_01delivering0000000000000',
        shortId: 'C-0005',
        node: 'deliver',
        status: 'delivering',
        lines: [{ label: 'toy-web#6', title: 'Announce form errors' }],
        itemsRead: true,
        costUsd: 0,
        costPartial: false,
        estimatedCostUsd: null,
      },
    ]);
    await fixture.whenStable();
    const row = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="in-motion-row"]');

    expect(row?.querySelector('[data-testid="glance-work-ref"]')?.textContent?.trim()).toBe('toy-web#6');
    expect(row?.querySelector('[data-testid="glance-title"]')?.textContent?.trim()).toBe('Announce form errors');
    expect(row?.querySelector('[data-testid="glance-row-status"]')?.textContent?.trim()).toBe('delivering');
  });

  it("holds a row's title in a skeleton until its work items are read", async () => {
    const fixture = render({ upNextState: 'ready' });
    fixture.componentRef.setInput('upNext', [
      { chunkId: 'ch_01unread000000000000000000', shortId: 'C-0006', node: 'build', lines: [{ label: 'toy-api#1', title: null }], itemsRead: false },
    ]);
    await fixture.whenStable();
    const row = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="up-next-row"]');

    expect(row?.querySelector('[data-testid="glance-work-ref"]')?.textContent?.trim()).toBe('toy-api#1');
    expect(row?.querySelector('[data-testid="glance-title-skeleton"]')).not.toBeNull();
    expect(row?.querySelector('[data-testid="glance-title"]')).toBeNull();
    expect(row?.querySelector('[data-testid="glance-row-status"]')).toBeNull();
  });

  it('heads a row with no work item with its short id alone, and no empty work-ref', async () => {
    const fixture = render({ upNextState: 'ready' });
    fixture.componentRef.setInput('upNext', [{ chunkId: 'ch_01bare00000000000000000000', shortId: 'C-0007', node: 'build', lines: [], itemsRead: true }]);
    await fixture.whenStable();
    const row = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="up-next-row"]');

    expect(row?.querySelector('.head')?.textContent?.trim()).toBe('C-0007');
    expect(row?.querySelector('[data-testid="glance-work-ref"]')).toBeNull();
  });

  it("names a grouped chunk's later work items each under its own label", async () => {
    const fixture = render({ upNextState: 'ready' });
    fixture.componentRef.setInput('upNext', [
      {
        chunkId: 'ch_01group000000000000000000',
        shortId: 'C-0008',
        node: 'build',
        lines: [
          { label: 'toy-api#1', title: 'Rate-limit login' },
          { label: 'toy-web#3', title: 'Drop moment.js' },
        ],
        itemsRead: true,
      },
    ]);
    await fixture.whenStable();
    const row = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="up-next-row"]');

    expect([...(row?.querySelectorAll('[data-testid="glance-work-ref"]') ?? [])].map((el) => el.textContent?.trim())).toEqual(['toy-api#1', 'toy-web#3']);
    expect([...(row?.querySelectorAll('[data-testid="glance-title"]') ?? [])].map((el) => el.textContent?.trim())).toEqual(['Rate-limit login', 'Drop moment.js']);
  });
});
