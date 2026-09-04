import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetFindingList, type FindingListRowVm } from './finding-list';
import type { KitAsyncStateValue } from '../kit/kit-async-state';

const LIVE_ROW: FindingListRowVm = {
  findingId: 'fin_01M1KANH0RZEABSD44RCEH6G9B',
  findingClass: 'stale-docstring',
  locus: 'src/a.py:1',
  summary: 'docstring narrates a removed parameter',
  state: 'live',
  lastSeenAt: '2026-01-05T00:00:00Z',
};

const GONE_ROW: FindingListRowVm = {
  findingId: 'fin_2',
  findingClass: 'unused-import',
  locus: 'src/b.py:4',
  summary: 'import no longer referenced',
  state: 'gone',
  lastSeenAt: '2026-01-06T00:00:00Z',
};

const RESOLVED_ROW: FindingListRowVm = {
  findingId: 'fin_3',
  findingClass: 'stale-docstring',
  locus: 'src/c.py:9',
  summary: 'docstring rewritten to match the signature',
  state: 'resolved',
  lastSeenAt: null,
};

const ROWS: readonly FindingListRowVm[] = [LIVE_ROW, GONE_ROW, RESOLVED_ROW];

describe('FleetFindingList', () => {
  async function mount(inputs: {
    rows?: readonly FindingListRowVm[];
    state?: KitAsyncStateValue;
    selectedId?: string | null;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetFindingList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetFindingList);
    fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    if (inputs.selectedId !== undefined) fixture.componentRef.setInput('selectedId', inputs.selectedId);
    await fixture.whenStable();
    return fixture;
  }

  it('renders every row with its summary headline, class, compact ref, and locus', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-finding-row-fin_01M1KANH0RZEABSD44RCEH6G9B"]');
    expect(row?.textContent).toContain('stale-docstring');
    expect(row?.textContent).toContain('F-6G9B');
    expect(row?.textContent).toContain('src/a.py:1');
    expect(row?.textContent).toContain('docstring narrates a removed parameter');
    expect(row?.querySelector('[title]')?.getAttribute('title')).toBe('fin_01M1KANH0RZEABSD44RCEH6G9B');
  });

  it('clamps the summary headline to three lines, never through fleet-kit-prose-block', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-finding-row-fin_01M1KANH0RZEABSD44RCEH6G9B"]');
    const headline = row?.querySelector('.fl-summary');
    expect(headline).toBeTruthy();
    expect(headline?.textContent).toContain('docstring narrates a removed parameter');
    expect(row?.querySelector('fleet-kit-prose-block')).toBeNull();
  });

  it('right-aligns the compact ref beside the class, carrying the full id as its title', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-finding-row-fin_01M1KANH0RZEABSD44RCEH6G9B"]');
    const ref = row?.querySelector('.fl-ref');
    expect(ref?.textContent?.trim()).toBe('F-6G9B');
    expect(ref?.getAttribute('title')).toBe('fin_01M1KANH0RZEABSD44RCEH6G9B');
  });

  it('renders the most recent observation via fleet-when', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-finding-row-fin_01M1KANH0RZEABSD44RCEH6G9B"]');
    expect(row?.querySelector('.fl-seen fleet-when')).toBeTruthy();
  });

  it('renders a dash when a row carries no last-seen instant', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-finding-row-fin_3"]');
    expect(row?.querySelector('.fl-seen fleet-when')).toBeNull();
    expect(row?.querySelector('.fl-seen-when')?.textContent?.trim()).toBe('—');
  });

  it("names each row's own state, right-aligned on the last-seen line", async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    for (const [findingId, state] of [
      ['fin_01M1KANH0RZEABSD44RCEH6G9B', 'live'],
      ['fin_2', 'gone'],
      ['fin_3', 'resolved'],
    ] as const) {
      const badge = el.querySelector(`[data-testid="gardening-finding-state-${findingId}"]`);
      expect(badge?.textContent?.trim(), findingId).toBe(state);
      // On the last-seen line, not a line of its own.
      expect(badge?.closest('.fl-seen'), findingId).toBeTruthy();
    }
  });

  it('colors an open, a flagged, and an exited state differently, off the shared mapping', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const toneOf = (findingId: string) =>
      el
        .querySelector(`[data-testid="gardening-finding-state-${findingId}"] .badge`)
        ?.getAttribute('style');

    const live = toneOf('fin_01M1KANH0RZEABSD44RCEH6G9B');
    const gone = toneOf('fin_2');
    const resolved = toneOf('fin_3');
    expect(live).toBeTruthy();
    expect(live).not.toBe(gone);
    expect(live).not.toBe(resolved);
    expect(gone).not.toBe(resolved);
  });

  it('drops observed count and introduced from the row — those stay on the panel', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-finding-row-fin_01M1KANH0RZEABSD44RCEH6G9B"]');
    expect(row?.textContent).not.toContain('observed');
    expect(row?.textContent).not.toContain('introduced');
  });

  it('renders a gone-flagged row tinted, and still as a normal, present row', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const live = el.querySelector('[data-testid="gardening-finding-row-fin_01M1KANH0RZEABSD44RCEH6G9B"]');
    const gone = el.querySelector('[data-testid="gardening-finding-row-fin_2"]');
    expect(live?.querySelector('.fl-body--gone')).toBeNull();
    expect(gone?.querySelector('.fl-body--gone')).toBeTruthy();
    expect(gone?.querySelector('.fl-body--exited')).toBeNull();
    expect(gone?.textContent).toContain('unused-import');
  });

  it('renders an exited row dimmed but present, never removed', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const resolved = el.querySelector('[data-testid="gardening-finding-row-fin_3"]');
    expect(resolved).toBeTruthy();
    expect(resolved?.querySelector('.fl-body--exited')).toBeTruthy();
    expect(resolved?.querySelector('.fl-body--gone')).toBeNull();
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

  it('renders no checkbox and no bulk bar — triage is single-finding only, dispatched from the panel a row opens', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-findings-select-all"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-finding-select-fin_01M1KANH0RZEABSD44RCEH6G9B"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-findings-bulk-bar"]')).toBeNull();
  });

  describe('opening a finding in the right-hand panel', () => {
    it('emits findingPick with the row id on click', async () => {
      const fixture = await mount({});
      const el = fixture.nativeElement as HTMLElement;
      let emitted: string | undefined;
      fixture.componentInstance.findingPick.subscribe((findingId) => (emitted = findingId));

      el.querySelector<HTMLButtonElement>('[data-testid="gardening-finding-row-fin_2"]')!.click();

      expect(emitted).toBe('fin_2');
    });

    it('reflects selectedId onto the kit row', async () => {
      const fixture = await mount({ selectedId: 'fin_2' });
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="gardening-finding-row-fin_2"]')?.classList.contains('selected')).toBe(
        true,
      );
      expect(
        el.querySelector('[data-testid="gardening-finding-row-fin_01M1KANH0RZEABSD44RCEH6G9B"]')?.classList.contains(
          'selected',
        ),
      ).toBe(false);
    });
  });
});
