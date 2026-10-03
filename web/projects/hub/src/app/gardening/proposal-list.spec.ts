import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { FleetProposalList, type ProposalListRowVm } from './proposal-list';

const ROWS: readonly ProposalListRowVm[] = [
  {
    proposalId: 'gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2',
    title: 'Author a docstring standard',
    proposalClass: 'fix-the-source',
    waiting: true,
    createdAt: new Date(2026, 6, 18, 9, 5, 3).toISOString(),
  },
  {
    proposalId: 'gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y3',
    title: 'Delete the dead helper',
    proposalClass: 'remediate',
    waiting: false,
    createdAt: new Date(2026, 6, 17, 9, 5, 3).toISOString(),
  },
];

describe('FleetProposalList', () => {
  beforeEach(() => {
    vi.setSystemTime(new Date(2026, 6, 18, 15, 30));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

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

  it('sits inside the kit panel shell, labelled Proposals', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-proposal-list-panel"]');
    expect(panel?.tagName.toLowerCase()).toBe('fleet-kit-panel');
    expect(panel?.textContent).toContain('Proposals');
  });

  it('renders every row with its title, class, and compact ref', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const waiting = el.querySelector('[data-testid="gardening-proposal-row-gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2"]');
    expect(waiting?.textContent).toContain('Author a docstring standard');
    expect(waiting?.textContent).toContain('fix-the-source');
    expect(waiting?.textContent).toContain('GP-X1Y2');
    expect(waiting?.querySelector('[data-testid="gardening-proposal-row-closed"]')).toBeNull();

    const closed = el.querySelector('[data-testid="gardening-proposal-row-gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y3"]');
    expect(closed?.querySelector('[data-testid="gardening-proposal-row-closed"]')).toBeTruthy();
  });

  it('renders the created timestamp as a third line, below the meta line', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const waiting = el.querySelector('[data-testid="gardening-proposal-row-gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2"]');
    const meta = waiting?.querySelector('.pl-meta');
    const created = waiting?.querySelector('.pl-created');
    expect(created?.querySelector('fleet-when')?.textContent?.trim()).toBe('09:05');
    expect(meta?.nextElementSibling).toBe(created);
  });

  it('gives the ref a title attribute carrying the full id, and puts it last so it lands at the row end', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const waiting = el.querySelector('[data-testid="gardening-proposal-row-gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2"]');
    const ref = waiting?.querySelector('.pl-ref');
    expect(ref?.getAttribute('title')).toBe('gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2');
    expect(ref?.textContent).toBe('GP-X1Y2');

    const meta = waiting?.querySelector('.pl-meta');
    expect(meta?.lastElementChild).toBe(ref);
  });

  it('keeps the closed marker beside the class, ahead of the ref, on a closed row', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const closed = el.querySelector('[data-testid="gardening-proposal-row-gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y3"]');
    const meta = closed?.querySelector('.pl-meta');
    const children = Array.from(meta?.children ?? []);
    const classIndex = children.findIndex((c) => c.classList.contains('pl-class'));
    const closedIndex = children.findIndex((c) => c.classList.contains('pl-closed'));
    const refIndex = children.findIndex((c) => c.classList.contains('pl-ref'));
    expect(classIndex).toBeLessThan(closedIndex);
    expect(closedIndex).toBeLessThan(refIndex);
    expect(refIndex).toBe(children.length - 1);
  });

  it('marks the selected row', async () => {
    const fixture = await mount({ selectedId: 'gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposal-row-gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y2"]')?.classList).toContain('selected');
    expect(el.querySelector('[data-testid="gardening-proposal-row-gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y3"]')?.classList).not.toContain('selected');
  });

  it('emits proposalPick with the clicked row id', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.proposalPick.subscribe((id) => (emitted = id));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-proposal-row-gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y3"]')?.click();

    expect(emitted).toBe('gprop_01K4M2P3Q4R5S6T7U8V9W0X1Y3');
  });

  it('shows the empty state when there are no proposals', async () => {
    const fixture = await mount({ rows: [], state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-proposals-empty"]')).toBeTruthy();
  });
});
