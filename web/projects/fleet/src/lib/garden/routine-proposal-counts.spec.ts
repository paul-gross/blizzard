import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetRoutineProposalCounts, type ProposalCountsRowVm } from './routine-proposal-counts';

const ROWS: readonly ProposalCountsRowVm[] = [
  { proposalClass: 'stale-docstring', created: 6, open: 2, passed: 1, acceptedWithItem: 3, acceptedWithoutItem: 0 },
  { proposalClass: 'dead-code', created: 3, open: 0, passed: 2, acceptedWithItem: 0, acceptedWithoutItem: 1 },
];

describe('FleetRoutineProposalCounts', () => {
  async function mount(inputs: { rows?: readonly ProposalCountsRowVm[]; state?: 'loading' | 'error' | 'empty' | 'ready' }) {
    await TestBed.configureTestingModule({
      imports: [FleetRoutineProposalCounts],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetRoutineProposalCounts);
    fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    await fixture.whenStable();
    return fixture;
  }

  it('sits inside a kit panel labelled Proposal counts', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-routine-proposal-counts-panel"]');
    expect(panel?.tagName.toLowerCase()).toBe('fleet-kit-panel');
    expect(panel?.querySelector('.lbl')?.textContent).toContain('Proposal counts');
  });

  it('renders one row per class with its five counts', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const table = el.querySelector('[data-testid="gardening-routine-proposal-counts-table"]');
    expect(table?.tagName.toLowerCase()).toBe('table');

    const staleRow = el.querySelector('[data-testid="gardening-routine-proposal-counts-stale-docstring"]');
    expect(staleRow?.textContent).toContain('stale-docstring');
    const staleCells = Array.from(staleRow?.querySelectorAll('td') ?? []).map((td) => td.textContent);
    expect(staleCells).toEqual(['stale-docstring', '6', '2', '1', '3', '0']);

    const deadCodeRow = el.querySelector('[data-testid="gardening-routine-proposal-counts-dead-code"]');
    expect(deadCodeRow?.textContent).toContain('dead-code');
  });

  it('renders the empty state through fleet-kit-async-state rather than a blank table', async () => {
    const fixture = await mount({ rows: [], state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    const empty = el.querySelector('[data-testid="gardening-routine-proposal-counts-empty"]');
    expect(empty?.textContent).toContain('No garden proposals in this window.');
    expect(el.querySelector('[data-testid="gardening-routine-proposal-counts-table"]')).toBeNull();
  });

  it('renders no rows while loading', async () => {
    const fixture = await mount({ rows: [], state: 'loading' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-proposal-counts-table"]')).toBeNull();
  });
});
