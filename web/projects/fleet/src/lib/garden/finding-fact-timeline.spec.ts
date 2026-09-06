import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { FindingFactView } from '../api/hub';
import { FleetFindingFactTimeline } from './finding-fact-timeline';

const ADD_ONLY: FindingFactView[] = [{ kind: 'add', recorded_at: '2026-01-01T00:00:00Z' }];

const EXIT_THEN_REOPEN: FindingFactView[] = [
  {
    kind: 'add',
    recorded_at: '2026-01-01T00:00:00Z',
  },
  {
    kind: 'wont-fix',
    recorded_at: '2026-01-02T00:00:00Z',
    note: 'not worth the churn',
    actor: 'u_1',
  },
  {
    kind: 'reopened',
    recorded_at: '2026-01-03T00:00:00Z',
    note: 'actually still matters',
    actor: 'u_2',
  },
];

const NEWEST_NOTELESS: FindingFactView[] = [
  { kind: 'add', recorded_at: '2026-01-01T00:00:00Z' },
  { kind: 'observed', recorded_at: '2026-01-02T00:00:00Z' },
];

async function mount(facts: readonly FindingFactView[]) {
  await TestBed.configureTestingModule({
    imports: [FleetFindingFactTimeline],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetFindingFactTimeline);
  fixture.componentRef.setInput('facts', facts);
  await fixture.whenStable();
  return fixture;
}

describe('FleetFindingFactTimeline', () => {
  it('renders one row, no note and no actor, for an add-only chain', async () => {
    const fixture = await mount(ADD_ONLY);
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="finding-fact-row"]');
    expect(rows.length).toBe(1);
    expect(rows[0].querySelector('[data-testid="finding-fact-kind"]')?.textContent).toBe('Added');
    expect(rows[0].querySelector('[data-testid="finding-fact-note"]')).toBeNull();
    expect(rows[0].querySelector('[data-testid="finding-fact-actor"]')).toBeNull();
  });

  it('renders an exit fact followed by a reopened fact in order, each with its own note and actor', async () => {
    const fixture = await mount(EXIT_THEN_REOPEN);
    const el = fixture.nativeElement as HTMLElement;

    const rows = Array.from(el.querySelectorAll('[data-testid="finding-fact-row"]'));
    expect(rows.length).toBe(3);
    expect(rows.map((r) => r.querySelector('[data-testid="finding-fact-kind"]')?.textContent)).toEqual([
      'Added',
      "Won't fix",
      'Reopened',
    ]);

    expect(rows[1].querySelector('[data-testid="finding-fact-note"]')?.textContent).toBe('not worth the churn');
    expect(rows[1].querySelector('[data-testid="finding-fact-actor"]')?.textContent).toContain('u_1');

    expect(rows[2].querySelector('[data-testid="finding-fact-note"]')?.textContent).toBe('actually still matters');
    expect(rows[2].querySelector('[data-testid="finding-fact-actor"]')?.textContent).toContain('u_2');
  });

  it('renders no note for a chain whose newest fact carries none', async () => {
    const fixture = await mount(NEWEST_NOTELESS);
    const el = fixture.nativeElement as HTMLElement;

    const rows = Array.from(el.querySelectorAll('[data-testid="finding-fact-row"]'));
    expect(rows.length).toBe(2);
    expect(rows[1].querySelector('[data-testid="finding-fact-kind"]')?.textContent).toBe('Observed');
    expect(rows[1].querySelector('[data-testid="finding-fact-note"]')).toBeNull();
  });

  it('shows the empty state when facts is empty', async () => {
    const fixture = await mount([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="finding-fact-timeline"]')).toBeNull();
    expect(el.querySelector('[data-testid="finding-fact-timeline-empty"]')).toBeTruthy();
  });
});
