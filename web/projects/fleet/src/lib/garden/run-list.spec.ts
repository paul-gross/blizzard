import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetRunList, type RunListRowVm } from './run-list';
import type { KitAsyncStateValue } from '../kit/kit-async-state';

const ROWS: readonly RunListRowVm[] = [
  {
    chunkId: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    routineName: 'nightly',
    scopeSlug: 'blizzard',
    mode: 'full',
    mintedAt: '2026-01-10T00:00:00Z',
    outcome: 'done',
    escalated: false,
    counts: { added: 1, observed: 11, gone: 0 },
  },
  {
    chunkId: 'ch_2',
    routineName: 'nightly',
    scopeSlug: 'web',
    mode: 'delta',
    mintedAt: '2026-01-11T00:00:00Z',
    outcome: 'needs_human',
    escalated: true,
    counts: null,
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

  it('renders a row as three lines: routine/scope, mode + chunk ref, minted time', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-run-row-ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9"]');
    expect(row?.textContent).toContain('nightly/blizzard');
    expect(row?.textContent).toContain('Full');
  });

  it('renders the mode capitalised and the chunk ref right-aligned with the full id in title', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const ref = el.querySelector(
      '[data-testid="gardening-run-row-ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9"] [data-testid="rl-chunk-ref"]',
    );
    expect(ref?.textContent).toBe('C-3YJ9');
    expect(ref?.getAttribute('title')).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');

    const secondRow = el.querySelector('[data-testid="gardening-run-row-ch_2"]');
    expect(secondRow?.textContent).toContain('Delta');
  });

  it('renders the minted time through fleet-when', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-run-row-ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9"]');
    expect(row?.querySelector('fleet-when')).toBeTruthy();
  });

  describe('the counts triple', () => {
    async function countsText(counts: RunListRowVm['counts']): Promise<string | null | undefined> {
      const fixture = await mount({
        rows: [{ ...ROWS[0], counts }],
      });
      const el = fixture.nativeElement as HTMLElement;
      return el.querySelector('[data-testid="rl-counts"]')?.textContent;
    }

    it('joins added and observed by " / ", suppressing a zero gone leg', async () => {
      expect(await countsText({ added: 1, observed: 11, gone: 0 })).toBe('+1 / 11');
    });

    it('renders only the added leg when observed and gone are both zero', async () => {
      expect(await countsText({ added: 11, observed: 0, gone: 0 })).toBe('+11');
    });

    it('renders +0 in green when a run delivered sets but nothing was added, observed, or gone', async () => {
      const fixture = await mount({ rows: [{ ...ROWS[0], counts: { added: 0, observed: 0, gone: 0 } }] });
      const el = fixture.nativeElement as HTMLElement;
      const counts = el.querySelector('[data-testid="rl-counts"]');
      expect(counts?.textContent).toBe('+0');
      expect(counts?.querySelector('.rl-count-added--zero')).toBeTruthy();
    });

    it('colors a nonzero added leg red, not the zero-added green', async () => {
      const fixture = await mount({ rows: [{ ...ROWS[0], counts: { added: 1, observed: 11, gone: 0 } }] });
      const el = fixture.nativeElement as HTMLElement;
      const counts = el.querySelector('[data-testid="rl-counts"]');
      expect(counts?.querySelector('.rl-count-added')?.classList.contains('rl-count-added--zero')).toBe(false);
    });

    it('joins added and a nonzero gone leg even when observed is suppressed', async () => {
      expect(await countsText({ added: 2, observed: 0, gone: 3 })).toBe('+2 / -3');
    });

    it('joins all three legs when none are suppressed', async () => {
      expect(await countsText({ added: 1, observed: 11, gone: 5 })).toBe('+1 / 11 / -5');
    });

    it('renders nothing when the run delivered no sets at all, rather than +0', async () => {
      const fixture = await mount({ rows: [{ ...ROWS[0], counts: null }] });
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector('[data-testid="rl-counts"]')).toBeNull();
    });
  });

  it("states every row's outcome as text, so a non-clean run never rides on colour alone", async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    // The escalated row's red tint is emphasis on top of this word, never a
    // substitute for it: a reader who cannot resolve the colour still reads
    // "needs_human" and can tell the two rows apart.
    expect(el.querySelector('[data-testid="rl-outcome-ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9"]')?.textContent?.trim()).toBe(
      'done',
    );
    expect(el.querySelector('[data-testid="rl-outcome-ch_2"]')?.textContent?.trim()).toBe('needs_human');
  });

  it('marks an escalated row distinctly from a normal row', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const normal = el.querySelector('[data-testid="gardening-run-row-ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9"]');
    const escalated = el.querySelector('[data-testid="gardening-run-row-ch_2"]');
    expect(normal?.querySelector('.rl-body--escalated')).toBeNull();
    expect(escalated?.querySelector('.rl-body--escalated')).toBeTruthy();
  });

  it('emits runPick with the chunk id on click', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.runPick.subscribe((chunkId) => (emitted = chunkId));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-run-row-ch_2"]')!.click();

    expect(emitted).toBe('ch_2');
  });

  it('reflects selection onto the kit row, at its left edge (kit-select-row.css owns the accent)', async () => {
    const fixture = await mount({ selectedChunkId: 'ch_2' });
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-run-row-ch_2"]');
    expect(row?.classList.contains('selected')).toBe(true);
  });

  it('shows the empty state when there are no runs', async () => {
    const fixture = await mount({ rows: [], state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-runs-empty"]')).toBeTruthy();
  });
});
