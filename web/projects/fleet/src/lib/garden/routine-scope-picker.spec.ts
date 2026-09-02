import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { RoutineView, ScopeView } from '../api/hub';
import { FleetRoutineScopePicker } from './routine-scope-picker';

const ROUTINES: readonly RoutineView[] = [
  { routine_id: 'rt_1', name: 'nightly', graph_name: 'sweep', default_scope_slug: 'blizzard', created_at: '2026-01-01T00:00:00Z' },
  { routine_id: 'rt_2', name: 'weekly', graph_name: 'sweep', default_scope_slug: 'blizzard', created_at: '2026-01-01T00:00:00Z' },
];

const SCOPES: readonly ScopeView[] = [
  { slug: 'blizzard', description: 'the blizzard repo', created_at: '2026-01-01T00:00:00Z' },
  { slug: 'blizzard-context', description: 'the context repo', created_at: '2026-01-01T00:00:00Z' },
];

describe('FleetRoutineScopePicker', () => {
  async function mount(inputs: { selectedRoutine?: string | null; selectedScope?: string | null }) {
    await TestBed.configureTestingModule({
      imports: [FleetRoutineScopePicker],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetRoutineScopePicker);
    fixture.componentRef.setInput('routines', ROUTINES);
    fixture.componentRef.setInput('scopes', SCOPES);
    fixture.componentRef.setInput('selectedRoutine', inputs.selectedRoutine ?? null);
    fixture.componentRef.setInput('selectedScope', inputs.selectedScope ?? null);
    await fixture.whenStable();
    return fixture;
  }

  it('renders every routine and scope as an option', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const routineSelect = el.querySelector<HTMLSelectElement>('[data-testid="gardening-findings-routine-select"]')!;
    expect(Array.from(routineSelect.options).map((o) => o.value)).toEqual(['', 'nightly', 'weekly']);

    const scopeSelect = el.querySelector<HTMLSelectElement>('[data-testid="gardening-findings-scope-select"]')!;
    expect(Array.from(scopeSelect.options).map((o) => o.value)).toEqual(['', 'blizzard', 'blizzard-context']);
  });

  it('marks the selected routine and scope', async () => {
    const fixture = await mount({ selectedRoutine: 'weekly', selectedScope: 'blizzard-context' });
    const el = fixture.nativeElement as HTMLElement;

    const routineSelect = el.querySelector<HTMLSelectElement>('[data-testid="gardening-findings-routine-select"]')!;
    expect(routineSelect.value).toBe('weekly');
    const scopeSelect = el.querySelector<HTMLSelectElement>('[data-testid="gardening-findings-scope-select"]')!;
    expect(scopeSelect.value).toBe('blizzard-context');
  });

  it('emits routinePick with the chosen routine name', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.routinePick.subscribe((name) => (emitted = name));

    const routineSelect = el.querySelector<HTMLSelectElement>('[data-testid="gardening-findings-routine-select"]')!;
    routineSelect.value = 'weekly';
    routineSelect.dispatchEvent(new Event('change'));

    expect(emitted).toBe('weekly');
  });

  it('emits scopePick with the chosen scope slug', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | undefined;
    fixture.componentInstance.scopePick.subscribe((slug) => (emitted = slug));

    const scopeSelect = el.querySelector<HTMLSelectElement>('[data-testid="gardening-findings-scope-select"]')!;
    scopeSelect.value = 'blizzard-context';
    scopeSelect.dispatchEvent(new Event('change'));

    expect(emitted).toBe('blizzard-context');
  });
});
