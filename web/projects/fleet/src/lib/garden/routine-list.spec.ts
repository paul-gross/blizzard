import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetRoutineList, type RoutineListRowVm } from './routine-list';

const ROWS: readonly RoutineListRowVm[] = [
  { routineId: 'rtn_01ABCDEFGHJKMNPQRSTVWXYZ0123', name: 'nightly', graphName: 'garden-routine', blocked: false },
  {
    routineId: 'rtn_01FEDCBAZYXWVUTSRQPNMKJHG9876',
    name: 'weekly-audit',
    graphName: 'architecture',
    blocked: true,
  },
];

describe('FleetRoutineList', () => {
  async function mount(inputs: {
    rows?: readonly RoutineListRowVm[];
    state?: 'loading' | 'error' | 'empty' | 'ready';
    selectedName?: string | null;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetRoutineList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetRoutineList);
    fixture.componentRef.setInput('rows', inputs.rows ?? ROWS);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('selectedName', inputs.selectedName ?? null);
    await fixture.whenStable();
    return fixture;
  }

  it('renders every routine with its name and graph', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="gardening-routine-row-nightly"]');
    expect(row?.textContent).toContain('nightly');
    expect(row?.textContent).toContain('garden-routine');
  });

  it('renders the routine id as a compact ref, right-aligned, with the full id as its title', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    // `.rt-ref` is the ref's own right-alignment rule (`routine-list.css`'s
    // `margin-left: auto`, the trailing-element pattern `chunk-timeline-
    // selection.css`'s `.line2 .ts` already uses).
    const ref = el.querySelector('[data-testid="gardening-routine-row-nightly"] .rt-ref');
    expect(ref?.textContent).toBe('R-0123');
    expect(ref?.getAttribute('title')).toBe('rtn_01ABCDEFGHJKMNPQRSTVWXYZ0123');
  });

  it('marks a blocked routine distinctly from an unblocked one', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.textContent).not.toContain('blocked');
    expect(el.querySelector('[data-testid="gardening-routine-row-weekly-audit"]')?.textContent).toContain('blocked');
  });

  it('reflects selectedName onto the matching row only', async () => {
    const fixture = await mount({ selectedName: 'nightly' });
    const el = fixture.nativeElement as HTMLElement;

    expect(
      el.querySelector('[data-testid="gardening-routine-row-nightly"]')?.classList.contains('selected'),
    ).toBe(true);
    expect(
      el.querySelector('[data-testid="gardening-routine-row-weekly-audit"]')?.classList.contains('selected'),
    ).toBe(false);
  });

  it('emits routinePick with the routine name on a row click', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.routinePick.subscribe((name) => (emitted = name));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-row-weekly-audit"]')?.click();

    expect(emitted).toBe('weekly-audit');
  });

  it('shows the empty state when there are no routines', async () => {
    const fixture = await mount({ rows: [], state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routines-empty"]')).toBeTruthy();
  });
});
