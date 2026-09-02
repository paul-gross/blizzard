import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetRunList, type RunListRowVm } from './run-list';
import type { KitAsyncStateValue } from '../kit/kit-async-state';

const ROWS: readonly RunListRowVm[] = [
  {
    chunkId: 'ch_1',
    routineName: 'nightly',
    scopeSlug: 'blizzard',
    mode: 'full',
    mintedAt: '2026-01-10T00:00:00Z',
    outcome: 'done',
    escalated: false,
    delivered: [
      { findingSetId: 'fins_1', revisionsLabel: 'blizzard@abc123', measurement: '3 findings' },
      { findingSetId: 'fins_2', revisionsLabel: 'blizzard@def456', measurement: null },
    ],
  },
  {
    chunkId: 'ch_2',
    routineName: 'nightly',
    scopeSlug: 'web',
    mode: 'delta',
    mintedAt: '2026-01-11T00:00:00Z',
    outcome: 'needs_human',
    escalated: true,
    delivered: [],
  },
];

describe('FleetRunList', () => {
  async function mount(inputs: {
    rows?: readonly RunListRowVm[];
    state?: KitAsyncStateValue;
    selectedChunkId?: string | null;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetRunList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetRunList);
    fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('selectedChunkId', inputs.selectedChunkId ?? null);
    await fixture.whenStable();
    return fixture;
  }

  it('renders every row with its routine, scope, mode, and outcome', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-run-row-ch_1"]');
    expect(row?.textContent).toContain('nightly/blizzard');
    expect(row?.textContent).toContain('mode=full');
    expect(row?.textContent).toContain('done');
  });

  it('keeps every delivered finding set its own distinct entry, never merged', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const sets = el.querySelectorAll('[data-testid="gardening-run-row-ch_1"] [data-testid^="gardening-run-set-"]');
    expect(sets).toHaveLength(2);
    expect(sets[0].textContent).toContain('abc123');
    expect(sets[1].textContent).toContain('def456');
  });

  it('marks an escalated row distinctly from a normal row', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const normal = el.querySelector('[data-testid="gardening-run-row-ch_1"]');
    const escalated = el.querySelector('[data-testid="gardening-run-row-ch_2"]');
    expect(normal?.classList.contains('rl-row--escalated')).toBe(false);
    expect(escalated?.classList.contains('rl-row--escalated')).toBe(true);
    expect(escalated?.querySelector('[data-testid="rl-escalated-note"]')).toBeTruthy();
  });

  it('emits runPick with the chunk id on click', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.runPick.subscribe((chunkId) => (emitted = chunkId));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-run-row-ch_1"]')!.click();

    expect(emitted).toBe('ch_1');
  });

  it('names the run list CLI verb', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).toContain('hub run list');
  });

  it('shows the empty state when there are no runs', async () => {
    const fixture = await mount({ rows: [], state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-runs-findings-empty"]')).toBeTruthy();
  });
});
