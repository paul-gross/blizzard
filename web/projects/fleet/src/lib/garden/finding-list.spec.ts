import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetFindingList, type FindingListRowVm } from './finding-list';
import type { KitAsyncStateValue } from '../kit/kit-async-state';

const LIVE_ROW: FindingListRowVm = {
  findingId: 'fnd_1',
  findingClass: 'stale-docstring',
  locus: 'src/a.py:1',
  summary: 'docstring narrates a removed parameter',
  introduced: '2026-01-01T00:00:00Z',
  lastSeenAt: '2026-01-05T00:00:00Z',
  observedCount: 3,
  state: 'live',
  note: null,
  workItem: null,
};

const GONE_ROW: FindingListRowVm = {
  findingId: 'fnd_2',
  findingClass: 'unused-import',
  locus: 'src/b.py:4',
  summary: 'import no longer referenced',
  introduced: '2026-01-02T00:00:00Z',
  lastSeenAt: '2026-01-04T00:00:00Z',
  observedCount: 2,
  state: 'gone',
  note: 'not seen in the last sweep',
  workItem: null,
};

const RESOLVED_ROW: FindingListRowVm = {
  findingId: 'fnd_3',
  findingClass: 'stale-docstring',
  locus: 'src/c.py:9',
  summary: 'docstring rewritten to match the signature',
  introduced: '2026-01-03T00:00:00Z',
  lastSeenAt: '2026-01-06T00:00:00Z',
  observedCount: 1,
  state: 'resolved',
  note: 'fixed in the same pass',
  workItem: null,
};

const ROWS: readonly FindingListRowVm[] = [LIVE_ROW, GONE_ROW, RESOLVED_ROW];

describe('FleetFindingList', () => {
  async function mount(inputs: {
    rows?: readonly FindingListRowVm[];
    state?: KitAsyncStateValue;
    canControl?: boolean;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetFindingList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetFindingList);
    fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('canControl', inputs.canControl ?? false);
    await fixture.whenStable();
    return fixture;
  }

  it('renders every row with its class, locus, and summary', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-finding-row-fnd_1"]');
    expect(row?.textContent).toContain('stale-docstring');
    expect(row?.textContent).toContain('src/a.py:1');
    expect(row?.textContent).toContain('docstring narrates a removed parameter');
    expect(row?.textContent).toContain('observed x3');
  });

  it('renders a gone-flagged row tinted, and still as a normal, present row', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const live = el.querySelector('[data-testid="gardening-finding-row-fnd_1"]');
    const gone = el.querySelector('[data-testid="gardening-finding-row-fnd_2"]');
    expect(live?.classList.contains('fl-row--gone')).toBe(false);
    expect(gone?.classList.contains('fl-row--gone')).toBe(true);
    expect(gone?.classList.contains('fl-row--exited')).toBe(false);
    expect(gone?.textContent).toContain('unused-import');
  });

  it("renders a note on a gone row, visible on the row itself", async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const gone = el.querySelector('[data-testid="gardening-finding-row-fnd_2"]');
    expect(gone?.querySelector('[data-testid="fl-note"]')?.textContent).toContain('not seen in the last sweep');
  });

  it('renders an exited row dimmed but present, never removed, with its own note', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const resolved = el.querySelector('[data-testid="gardening-finding-row-fnd_3"]');
    expect(resolved).toBeTruthy();
    expect(resolved?.classList.contains('fl-row--exited')).toBe(true);
    expect(resolved?.classList.contains('fl-row--gone')).toBe(false);
    expect(resolved?.querySelector('[data-testid="fl-note"]')?.textContent).toContain('fixed in the same pass');
  });

  it('renders a work item beside a finding, still rendered with its own live state', async () => {
    const fixture = await mount({
      rows: [{ ...LIVE_ROW, workItem: { label: 'hub#42', webUrl: '/board/chunk/ch_1' } }],
    });
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-finding-row-fnd_1"]');
    const link = row?.querySelector<HTMLAnchorElement>('[data-testid="fl-work-item-link"]');
    expect(link?.textContent).toBe('hub#42');
    expect(link?.getAttribute('href')).toBe('/board/chunk/ch_1');
    expect(row?.textContent).toContain('live');
  });

  it('names the finding list CLI verb', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).toContain('hub finding list');
  });

  it('shows the empty state when there are no findings', async () => {
    const fixture = await mount({ rows: [], state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-findings-empty"]')).toBeTruthy();
  });

  describe('multi-select and the bulk bar (blizzard#401 Phase 3, D9)', () => {
    it('renders no checkbox and no bulk bar at all when canControl is false', async () => {
      const fixture = await mount({ canControl: false });
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="gardening-findings-select-all"]')).toBeNull();
      expect(el.querySelector('[data-testid="gardening-finding-select-fnd_1"]')).toBeNull();
      expect(el.querySelector('[data-testid="gardening-findings-bulk-bar"]')).toBeNull();
    });

    it('selects and clears every visible row via the select-all checkbox', async () => {
      const fixture = await mount({ canControl: true });
      const el = fixture.nativeElement as HTMLElement;

      const selectAll = el.querySelector<HTMLInputElement>('[data-testid="gardening-findings-select-all"]')!;
      expect(selectAll.checked).toBe(false);

      selectAll.click();
      await fixture.whenStable();

      expect(el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_1"]')!.checked).toBe(
        true,
      );
      expect(el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_2"]')!.checked).toBe(
        true,
      );
      expect(el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_3"]')!.checked).toBe(
        true,
      );
      expect(el.querySelector<HTMLInputElement>('[data-testid="gardening-findings-select-all"]')!.checked).toBe(
        true,
      );

      el.querySelector<HTMLInputElement>('[data-testid="gardening-findings-select-all"]')!.click();
      await fixture.whenStable();

      expect(el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_1"]')!.checked).toBe(
        false,
      );
      expect(el.querySelector('[data-testid="gardening-findings-bulk-bar"]')).toBeNull();
    });

    it('marks the select-all checkbox indeterminate when only some visible rows are selected', async () => {
      const fixture = await mount({ canControl: true });
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_1"]')!.click();
      await fixture.whenStable();

      const selectAll = el.querySelector<HTMLInputElement>('[data-testid="gardening-findings-select-all"]')!;
      expect(selectAll.indeterminate).toBe(true);
      expect(selectAll.checked).toBe(false);
    });

    it('emits bulkTriage with the right verb and the ordered selected ids', async () => {
      const fixture = await mount({ canControl: true });
      const el = fixture.nativeElement as HTMLElement;
      let emitted: { verb: string; findingIds: readonly string[] } | undefined;
      fixture.componentInstance.bulkTriage.subscribe((event) => (emitted = event));

      el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_2"]')!.click();
      await fixture.whenStable();
      el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_1"]')!.click();
      await fixture.whenStable();

      el.querySelector<HTMLButtonElement>('[data-testid="gardening-finding-bulk-resolve"]')!.click();
      await fixture.whenStable();

      expect(emitted).toEqual({ verb: 'resolve', findingIds: ['fnd_1', 'fnd_2'] });
    });

    it('offers the Reopen button only when every selected row is exited', async () => {
      const fixture = await mount({ canControl: true });
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_1"]')!.click();
      await fixture.whenStable();
      expect(el.querySelector('[data-testid="gardening-finding-bulk-reopen"]')).toBeNull();

      el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_1"]')!.click();
      el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_3"]')!.click();
      await fixture.whenStable();
      expect(el.querySelector('[data-testid="gardening-finding-bulk-reopen"]')).toBeTruthy();

      el.querySelector<HTMLInputElement>('[data-testid="gardening-finding-select-fnd_1"]')!.click();
      await fixture.whenStable();
      expect(el.querySelector('[data-testid="gardening-finding-bulk-reopen"]')).toBeNull();
    });
  });
});
