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
