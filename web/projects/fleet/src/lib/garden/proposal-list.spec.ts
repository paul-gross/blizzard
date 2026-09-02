import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetProposalList, type ProposalListRowVm } from './proposal-list';

const ROWS: readonly ProposalListRowVm[] = [
  { proposalId: 'gp_1', title: 'Author a docstring standard', proposalClass: 'fix-the-source', waiting: true },
  { proposalId: 'gp_2', title: 'Delete the dead helper', proposalClass: 'remediate', waiting: false },
];

describe('FleetProposalList', () => {
  async function mount(inputs: {
    rows?: readonly ProposalListRowVm[];
    selectedId?: string | null;
    state?: 'loading' | 'error' | 'empty' | 'ready';
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetProposalList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetProposalList);
    fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
    fixture.componentRef.setInput('selectedId', inputs.selectedId ?? null);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    await fixture.whenStable();
    return fixture;
  }

  it('renders every row with its title and class', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const waiting = el.querySelector('[data-testid="gardening-proposal-row-gp_1"]');
    expect(waiting?.textContent).toContain('Author a docstring standard');
    expect(waiting?.textContent).toContain('fix-the-source');
    expect(waiting?.querySelector('[data-testid="gardening-proposal-row-closed"]')).toBeNull();

    const closed = el.querySelector('[data-testid="gardening-proposal-row-gp_2"]');
    expect(closed?.querySelector('[data-testid="gardening-proposal-row-closed"]')).toBeTruthy();
  });

  it('marks the selected row', async () => {
    const fixture = await mount({ selectedId: 'gp_1' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_1"]')?.classList).toContain('pl-row--selected');
    expect(el.querySelector('[data-testid="gardening-proposal-row-gp_2"]')?.classList).not.toContain(
      'pl-row--selected',
    );
  });

  it('emits proposalPick with the clicked row id', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.proposalPick.subscribe((id) => (emitted = id));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-proposal-row-gp_2"]')?.click();

    expect(emitted).toBe('gp_2');
  });

  it('shows the empty state when there are no proposals', async () => {
    const fixture = await mount({ rows: [], state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposals-empty"]')).toBeTruthy();
  });
});
