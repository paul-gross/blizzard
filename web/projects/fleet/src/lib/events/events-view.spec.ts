import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { EventsView } from './events-view';

const EVENTS = [
  {
    id: 2,
    recorded_at: '2026-07-16T00:00:02Z',
    severity: 'critical',
    kind: 'escalation-opened',
    runner_id: 'rn_02',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB',
    message: 'Runner escalated: build failed three times',
    lease_id: null,
  },
  {
    id: 1,
    recorded_at: '2026-07-16T00:00:01Z',
    severity: 'info',
    kind: 'lease-minted',
    runner_id: 'rn_01',
    chunk_id: null,
    lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    message: 'Lease minted',
  },
];

describe('EventsView', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [EventsView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  function render(overrides: Record<string, unknown> = {}) {
    const fixture = TestBed.createComponent(EventsView);
    fixture.componentRef.setInput('events', overrides['events'] ?? EVENTS);
    for (const [key, value] of Object.entries(overrides)) {
      if (key === 'events') continue;
      fixture.componentRef.setInput(key, value);
    }
    return fixture;
  }

  it('renders every event handed to it, severity-ordered as given (server sort, not re-sorted)', async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="events-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector('[data-testid="events-message"]')?.textContent).toContain('escalated');
    expect(rows[1].querySelector('[data-testid="events-message"]')?.textContent).toContain('Lease minted');
    expect(el.querySelector('[data-testid="events-count"]')?.textContent).toContain('2');
  });

  it("reflects each row's severity on its badge", async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="events-row"]');
    expect(rows[0].querySelector('[data-testid="events-severity"]')?.textContent).toContain('critical');
    expect(rows[1].querySelector('[data-testid="events-severity"]')?.textContent).toContain('info');
  });

  it('renders the severity badge in the calm soft variant, not the saturated pill', async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const badge = el.querySelector('[data-testid="events-severity"] .badge');
    expect(badge?.classList.contains('soft')).toBe(true);
    expect(badge?.classList.contains('pill')).toBe(false);
  });

  it('lays each row out time-first: time, chunk, severity, kind, runner, message, lease', async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Row 1 is the one carrying a lease; row 0 carries the chunk.
    const rows = el.querySelectorAll('[data-testid="events-row"]');
    const order = (row: Element) =>
      [...row.children].map((c) => c.getAttribute('data-testid') ?? c.className);
    expect(order(rows[0])).toEqual([
      'events-time',
      'events-chunk',
      'events-severity',
      'events-kind',
      'events-runner',
      'events-message',
    ]);
    expect(order(rows[1])).toEqual([
      'events-time',
      'chunk-none',
      'events-severity',
      'events-kind',
      'events-runner',
      'events-message',
      'events-lease',
    ]);
  });

  it('lays the row out as a grid whose metadata tracks are all fixed, so they align down the feed', async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // jsdom has no layout engine, so the alignment itself is unassertable here — what is
    // assertable is that the row is a grid and that every metadata track is a fixed
    // length rather than content-sized (each row is its own grid container, so a
    // content-sized track re-measures per row and the column wanders).
    const style = getComputedStyle(el.querySelector('.ev') as HTMLElement);
    expect(style.display).toBe('grid');
    const tracks = style.gridTemplateColumns.split(' ');
    // time, chunk, severity, kind, runner — then the message (1fr) and the lease.
    expect(tracks.slice(0, 5).every((t) => t.endsWith('ch'))).toBe(true);
    expect(tracks.slice(5)).toEqual(['1fr', 'auto']);
  });

  it('falls back to a wrapping flex line below the board mobile cutoff, so no track can collapse', async () => {
    const fixture = render();
    await fixture.whenStable();

    // The regression this guards: the fixed metadata tracks are wider than a handset's
    // content box, so on a narrow viewport the 1fr message track resolves to 0 and — with
    // overflow-wrap: anywhere — wraps one character per line. jsdom has no layout engine
    // and does not evaluate @media against a viewport, so what is assertable here is that
    // the fallback rule EXISTS and targets the board's own breakpoint; the rendered result
    // is proven in the browser (tests/e2e/test_event_log_e2e.py).
    const styles = [...document.querySelectorAll('style')].map((s) => s.textContent ?? '');
    const rowStyles = styles.find((s) => s.includes('grid-template-columns'));
    expect(rowStyles).toBeDefined();
    const narrow = /@media\s*\(max-width:\s*767\.98px\)\s*\{(.*)\}/s.exec(rowStyles ?? '')?.[1] ?? '';
    // The row stops being a grid, and the message gets a full-width basis of its own.
    expect(narrow).toMatch(/display:\s*flex/);
    expect(narrow).toMatch(/flex-wrap:\s*wrap/);
    expect(narrow).toMatch(/flex:\s*1 1 100%/);
  });

  it('keeps the chunk column occupied on a chunk-less row so the grid stays aligned', async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="events-row"]');
    // No chunk button, but the slot itself is still there — the second grid cell.
    expect(rows[1].querySelector('[data-testid="events-chunk"]')).toBeNull();
    expect(rows[1].children[1].className).toBe('chunk-none');
  });

  it('renders a dash for a projected escalation row, which names no runner', async () => {
    const fixture = render({
      events: [{ ...EVENTS[0], runner_id: null, kind: 'needs-human' }],
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="events-runner"]')?.textContent?.trim()).toBe('—');
  });

  it('emits selectChunk when a row carrying a chunk id is activated', async () => {
    const fixture = render();
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="events-chunk"]')?.click();
    expect(selected).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YAB');
  });

  it('omits the chunk button for a runner-scoped event with no chunk id', async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="events-row"]');
    expect(rows[1].querySelector('[data-testid="events-chunk"]')).toBeNull();
  });

  it('renders the lease id as text when a row carries one', async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="events-row"]');
    expect(rows[1].querySelector('[data-testid="events-lease"]')?.textContent).toContain('L-3YJ9');
    expect(rows[0].querySelector('[data-testid="events-lease"]')).toBeNull();
  });

  it('emits filterChange when a severity chip is clicked', async () => {
    const fixture = render();
    let chosen: string | undefined;
    fixture.componentInstance.filterChange.subscribe((value) => (chosen = value));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="events-filter-critical"]')?.click();
    expect(chosen).toBe('critical');
  });

  it("renders each row's runner id, compact-ref'd", async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="events-row"]');
    expect(rows[0].querySelector('[data-testid="events-runner"]')?.textContent).toContain('R-02');
    expect(rows[1].querySelector('[data-testid="events-runner"]')?.textContent).toContain('R-01');
  });

  it('hides the runner and chunk filter rows when handed no id universe', async () => {
    const fixture = render();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="events-runner-filter"]')).toBeNull();
    expect(el.querySelector('[data-testid="events-chunk-filter"]')).toBeNull();
  });

  it('renders a runner chip per id and emits runnerFilterChange when one is clicked', async () => {
    const fixture = render({ runnerIds: ['rn_01', 'rn_02'] });
    let chosen: string | undefined;
    fixture.componentInstance.runnerFilterChange.subscribe((value) => (chosen = value));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="events-runner-filter"]')).not.toBeNull();
    // An "All" reset plus one chip per id.
    expect(el.querySelector('[data-testid="events-runner-filter-all"]')).not.toBeNull();
    el.querySelector<HTMLButtonElement>('[data-testid="events-runner-filter-rn_02"]')?.click();
    expect(chosen).toBe('rn_02');
  });

  it('renders a chunk chip per id and emits chunkFilterChange when one is clicked', async () => {
    const fixture = render({ chunkIds: ['ch_01KXKVVF1J3D6H6VYZ3XYN3YAB', 'ch_01KXKVVF1J3D6H6VYZ3XYN3ZZZ'] });
    let chosen: string | undefined;
    fixture.componentInstance.chunkFilterChange.subscribe((value) => (chosen = value));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="events-chunk-filter"]')).not.toBeNull();
    el.querySelector<HTMLButtonElement>(
      '[data-testid="events-chunk-filter-ch_01KXKVVF1J3D6H6VYZ3XYN3YAB"]',
    )?.click();
    expect(chosen).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YAB');
  });

  it('shows a loading state, distinct from empty', async () => {
    const fixture = render({ events: [], loading: true });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="events-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="events-empty"]')).toBeNull();
  });

  it('shows an error state, distinct from empty', async () => {
    const fixture = render({ events: [], error: true });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="events-error"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="events-empty"]')).toBeNull();
  });

  it('rests on an empty state with no events, once loaded', async () => {
    const fixture = render({ events: [] });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="events-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="events-count"]')).toBeNull();
  });
});
