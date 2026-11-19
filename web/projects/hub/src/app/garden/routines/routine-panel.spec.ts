import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';
import { provideRouter } from '@angular/router';

import type { UrgencyView } from 'fleet';
import type { RoutineEditDraft } from '../core/routine.domain';
import { FleetRoutinePanel, type RoutinePanelVm } from './routine-panel';
import { routineEditRules } from './routine.form';

const VM: RoutinePanelVm = {
  record: {
    slug: 'nightly',
    name: 'Nightly sweep',
    graphName: 'garden-routine',
    defaultScopeSlug: 'blizzard',
    modelEffort: 'claude-sonnet-5 · medium',
  },
  blockedReason: null,
  strategy: [
    { name: 'survey', prompt: 'Survey the repo for stale docstrings and dead code, one weed per finding.' },
    { name: 'deliver', prompt: null },
  ],
  trend: { created: 4, outflow: 2, withdrawn: 1, reopened: 1 },
  measurements: [
    { scopeSlug: 'blizzard', producedAt: '2026-01-10T00:00:00Z', measurement: '3 findings resolved this sweep' },
  ],
  lastSwept: [
    {
      scopeSlug: 'blizzard',
      findingSetId: 'fins_1',
      producedAt: '2026-01-10T00:00:00Z',
      revisionsLabel: 'blizzard@abc123',
    },
    { scopeSlug: 'never-swept', findingSetId: null, producedAt: null, revisionsLabel: '—' },
  ],
  windowLabel: 'last 28 days',
  relatedScopes: [{ slug: 'blizzard', isDefault: true }],
  retired: false,
  renderedRetired: false,
  recentRuns: [
    { chunkId: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9', scopeSlug: 'blizzard', mode: 'delta', findings: 3, mintedAt: '2026-01-10T00:00:00Z' },
  ],
  recentRunsState: 'ready',
  measurementsState: 'ready',
  runsLabel: '1 in the last 28 days',
  keptBy: [],
  keptByState: 'empty',
};

describe('FleetRoutinePanel', () => {
  async function mount(inputs: {
    vm?: RoutinePanelVm | null;
    state?: 'loading' | 'error' | 'empty' | 'ready';
    canEdit?: boolean;
    actionError?: string | null;
    lifecyclePending?: boolean;
    editable?: boolean;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetRoutinePanel],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetRoutinePanel);
    // `'vm' in inputs`, not `??`: an explicit `null` is the nothing-selected case
    // this panel has its own rendering for, and must not fall back to `VM`.
    fixture.componentRef.setInput('vm', 'vm' in inputs ? inputs.vm : VM);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('canEdit', inputs.canEdit ?? false);
    fixture.componentRef.setInput('actionError', inputs.actionError ?? null);
    fixture.componentRef.setInput('lifecyclePending', inputs.lifecyclePending ?? false);
    fixture.componentRef.setInput('editable', inputs.editable ?? inputs.canEdit ?? false);
    await fixture.whenStable();
    return fixture;
  }

  it('renders the select-a-routine rest state inside the Routine panel when nothing is selected', async () => {
    const fixture = await mount({ vm: null, state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-routine-panel"]');
    expect(panel?.tagName.toLowerCase()).toBe('fleet-kit-panel');
    const empty = panel?.querySelector('[data-testid="gardening-routine-panel-empty"]');
    expect(empty?.textContent).toContain('Select a routine');
    // The rest state is a panel awaiting a selection, not a bare line of text.
    expect(el.querySelector('[data-testid="gardening-routine-panel-empty"]')).toBe(empty!);
  });

  it('renders neither Activity nor Strategy while nothing is selected', async () => {
    const fixture = await mount({ vm: null, state: 'empty' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('fleet-kit-panel')).toHaveLength(1);
    expect(el.querySelector('[data-testid="gardening-routine-runtime"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-routine-strategy-panel"]')).toBeNull();
  });

  it('renders the record as a fact list: graph, default scope, model · effort, cadence and runs', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const record = el.querySelector('[data-testid="gardening-routine-record"]');
    expect(record?.textContent).toContain('Nightly sweep');
    const facts = el.querySelector('[data-testid="gardening-routine-record-facts"]');
    expect(facts?.tagName.toLowerCase()).toBe('dl');
    expect(Array.from(facts!.querySelectorAll('dt')).map((dt) => dt.textContent?.trim())).toEqual([
      'Graph',
      'Default scope',
      'Model · effort',
      'Kept by',
      'Runs',
    ]);
    expect(el.querySelector('[data-testid="gardening-routine-fact-graph"]')?.textContent).toContain(
      'garden-routine · tenant-wide library',
    );
    expect(el.querySelector('[data-testid="gardening-routine-fact-model"]')?.textContent).toContain(
      'claude-sonnet-5 · medium',
    );
    expect(el.querySelector('[data-testid="gardening-routine-fact-kept-by"]')?.textContent).toContain(
      'No cadence keeps it — it runs when started.',
    );
    expect(el.querySelector('[data-testid="gardening-routine-fact-runs"]')?.textContent).toContain('1 in the last 28 days');
  });

  it('names each chore keeping the routine, its cadence linked, and the next moment', async () => {
    const fixture = await mount({
      vm: {
        ...VM,
        keptBy: [
          { cadenceSlug: 'evening', cadenceName: 'Evening', choreKey: 'nightly', nextMoment: '2026-01-11T02:00:00Z' },
          { cadenceSlug: 'weekly', cadenceName: 'Weekly', choreKey: 'deep', nextMoment: null },
        ],
        keptByState: 'ready',
      },
    });
    const el = fixture.nativeElement as HTMLElement;

    const evening = el.querySelector('[data-testid="gardening-routine-kept-by-evening-nightly"]');
    expect(evening?.textContent).toContain('chore nightly');
    expect(evening?.textContent).toContain('next');
    expect(evening?.querySelector('a')?.getAttribute('href')).toBe('/gardening/cadences/evening');
    const weekly = el.querySelector('[data-testid="gardening-routine-kept-by-weekly-deep"]');
    expect(weekly?.textContent).toContain('chore deep');
    expect(weekly?.textContent).not.toContain('next');
    expect(el.querySelector('[data-testid="gardening-routine-kept-by-none"]')).toBeNull();
  });

  it('holds the Kept by fact loading while the cadences read is pending', async () => {
    const fixture = await mount({ vm: { ...VM, keptBy: null, keptByState: 'loading' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-kept-by-loading"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-kept-by-none"]')).toBeNull();
  });

  it('lists the recent runs, and says the window holds none once the read answers empty', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const run = el.querySelector('[data-testid="gardening-routine-recent-run-ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9"]');
    expect(run?.textContent).toContain('blizzard · delta · 3 findings');

    fixture.componentRef.setInput('vm', { ...VM, recentRuns: null, recentRunsState: 'loading', runsLabel: null });
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-routine-recent-runs-loading"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-recent-runs-empty"]')).toBeNull();

    fixture.componentRef.setInput('vm', { ...VM, recentRuns: [], recentRunsState: 'empty', runsLabel: '0 in the last 28 days' });
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-routine-recent-runs-empty"]')?.textContent).toContain(
      'No runs in the last 28 days.',
    );
  });

  it('reads loading, not "no measurement", until the sweeps read answers', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    fixture.componentRef.setInput('vm', { ...VM, measurements: [], measurementsState: 'loading' });
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-routine-measurements-loading"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-measurements"]')?.textContent).not.toContain(
      'No measurement recorded',
    );

    fixture.componentRef.setInput('vm', { ...VM, measurements: [], measurementsState: 'empty' });
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-routine-measurements-empty"]')?.textContent).toContain(
      'No measurement recorded in this window.',
    );
  });

  it('names the project beside the routine', async () => {
    const fixture = await mount({});
    fixture.componentRef.setInput('project', 'Blizzard');
    await fixture.whenStable();

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('[data-testid="gardening-routine-panel-project"]')?.textContent,
    ).toContain('Blizzard');
  });

  it('sits the definition inside a kit panel labelled Routine', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-routine-panel"]');
    expect(panel?.tagName.toLowerCase()).toBe('fleet-kit-panel');
    expect(panel?.querySelector('.lbl')?.textContent).toContain('Routine');
    expect(panel?.querySelector('[data-testid="gardening-routine-record"]')).toBeTruthy();
  });

  it('labels the strategy panel rather than repeating the word as a heading inside it', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="gardening-routine-strategy-panel"]');
    expect(panel?.querySelector('.lbl')?.textContent).toContain('Strategy');
    expect(panel?.querySelector('[data-testid="gardening-routine-strategy"] h4')).toBeNull();
  });

  it('leaves every panel body unscrolled, so the surrounding column is the only scroller', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const bodies = Array.from(el.querySelectorAll('fleet-kit-panel .p-body'));
    expect(bodies).toHaveLength(3);
    for (const body of bodies) expect(body.classList.contains('p-body--noscroll')).toBe(true);
  });

  it('offers Run… as the header\'s primary action, naming no CLI invocation, and emits run on click', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const actions = el.querySelector('[data-testid="gardening-routine-record"] [data-testid="gardening-routine-actions"]');
    const button = actions?.querySelector('[data-testid="gardening-routine-run"]');
    expect(button).toBeTruthy();
    expect(button?.textContent).toContain('Run…');
    expect(button?.classList.contains('primary')).toBe(true);
    expect(actions?.textContent).not.toContain('hub routine run');

    let emitted = false;
    fixture.componentInstance.run.subscribe(() => (emitted = true));
    (button as HTMLElement).click();
    expect(emitted).toBe(true);
  });

  it('shows the blocked notice and offers no run when blocked', async () => {
    const fixture = await mount({ vm: { ...VM, blockedReason: 'graph garden-routine has no effective mint' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-blocked"]')?.textContent).toContain('Blocked');
    expect(el.querySelector('[data-testid="gardening-routine-actions"]')).toBeNull();
  });

  it('still renders the strategy panel when blocked — the strategy is definition, not an offered run', async () => {
    const fixture = await mount({ vm: { ...VM, blockedReason: 'graph garden-routine has no effective mint' } });
    const el = fixture.nativeElement as HTMLElement;

    const strategyPanel = el.querySelector('[data-testid="gardening-routine-strategy-panel"]');
    expect(strategyPanel?.tagName.toLowerCase()).toBe('fleet-kit-panel');
    const strategy = strategyPanel?.querySelector('[data-testid="gardening-routine-strategy"]');
    expect(strategy?.querySelectorAll('fleet-kit-prose-block')).toHaveLength(2);
    expect(strategy?.textContent).toContain('Survey the repo for stale docstrings');
  });

  it('renders each strategy step through fleet-kit-prose-block with the context kind', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const strategyPanel = el.querySelector('[data-testid="gardening-routine-strategy-panel"]');
    const strategy = strategyPanel?.querySelector('[data-testid="gardening-routine-strategy"]');
    const blocks = strategy?.querySelectorAll('fleet-kit-prose-block');
    expect(blocks).toHaveLength(2);
    for (const block of Array.from(blocks ?? [])) {
      expect(block.querySelector('.prose--context')).toBeTruthy();
      expect(block.querySelector('.prose--output')).toBeNull();
    }
    expect(strategy?.textContent).toContain('Survey the repo for stale docstrings');
    expect(strategy?.textContent).toContain('(no prompt)');
  });

  it('stacks the record, runtime and strategy as three sibling kit panels, in that order', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const panels = Array.from(el.querySelectorAll('fleet-kit-panel'));
    expect(panels).toHaveLength(3);
    expect(panels.map((p) => p.getAttribute('data-testid'))).toEqual([
      'gardening-routine-panel',
      'gardening-routine-runtime',
      'gardening-routine-strategy-panel',
    ]);
    // Siblings, not nested — the column above them is the one scroller.
    for (const outer of panels) {
      for (const inner of panels) {
        if (outer !== inner) expect(outer.contains(inner)).toBe(false);
      }
    }
  });

  it('splits the runtime data into its own kit panel, separate from the record panel', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const definitionPanel = el.querySelector('[data-testid="gardening-routine-panel"]');
    const runtimePanel = el.querySelector('[data-testid="gardening-routine-runtime"]');
    expect(runtimePanel?.querySelector('[data-testid="gardening-routine-trend"]')).toBeTruthy();
    expect(runtimePanel?.querySelector('[data-testid="gardening-routine-measurements"]')).toBeTruthy();
    expect(runtimePanel?.querySelector('[data-testid="gardening-routine-last-swept"]')).toBeTruthy();
    expect(definitionPanel?.querySelector('[data-testid="gardening-routine-trend"]')).toBeNull();
    expect(definitionPanel?.querySelector('[data-testid="gardening-routine-strategy"]')).toBeNull();
  });

  it('renders the trend counts as a fact list, distinguishing created/outflow/withdrawn/reopened', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const facts = el.querySelector('[data-testid="gardening-routine-trend-facts"]');
    expect(facts?.tagName.toLowerCase()).toBe('dl');
    const dds = Array.from(facts?.querySelectorAll('dd') ?? []).map((d) => d.textContent);
    expect(dds).toEqual(['4', '2', '1', '1']);
    expect(el.querySelector('[data-testid="gardening-routine-trend"]')?.textContent).toContain('last 28 days');
  });

  it('renders the measurement series as agent-written prose', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const measurements = el.querySelector('[data-testid="gardening-routine-measurements"]');
    const block = measurements?.querySelector('fleet-kit-prose-block');
    expect(block?.querySelector('.prose--output')).toBeTruthy();
    expect(measurements?.textContent).toContain('3 findings resolved this sweep');
  });

  it('keys last-swept on scope, and marks a never-swept scope distinctly from a stale sweep', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const swept = el.querySelector('[data-testid="gardening-routine-last-swept-blizzard"]');
    expect(swept?.textContent).toContain('blizzard@abc123');
    const never = el.querySelector('[data-testid="gardening-routine-last-swept-never-swept"]');
    expect(never?.querySelector('[data-testid="gardening-routine-last-swept-never"]')?.textContent).toBe('never');
  });

  it('lists the related scopes, marking the routine default', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const section = el.querySelector('[data-testid="gardening-routine-scopes"]');
    expect(section?.textContent).toContain('blizzard');
    expect(section?.querySelector('[data-testid="gardening-routine-scope-default"]')).toBeTruthy();
  });

  it('omits the default marker for a non-default related scope, and says so when none are related', async () => {
    const fixture = await mount({
      vm: { ...VM, relatedScopes: [{ slug: 'other', isDefault: false }] },
    });
    const el = fixture.nativeElement as HTMLElement;

    const section = el.querySelector('[data-testid="gardening-routine-scopes"]');
    expect(section?.textContent).toContain('other');
    expect(section?.querySelector('[data-testid="gardening-routine-scope-default"]')).toBeNull();

    fixture.componentRef.setInput('vm', { ...VM, relatedScopes: [] });
    fixture.componentRef.setInput('relatedScopesState', 'empty');
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="gardening-routine-scopes"]')?.textContent).toContain(
      'No scope is related to this routine.',
    );
  });

  it('renders loading while the relation read is still pending', async () => {
    const fixture = await mount({ vm: { ...VM, relatedScopes: null } });
    const el = fixture.nativeElement as HTMLElement;

    fixture.componentRef.setInput('relatedScopesState', 'loading');
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-routine-scopes"]')?.textContent).toContain('Loading related scopes');
  });

  it('names no CLI read verb anywhere in the panel', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('hub routine get');
    expect(el.textContent).not.toContain('hub routine trend');
    expect(el.textContent).not.toContain('hub routine sweeps');
  });

  // --- Lifecycle: retire/enable, `FleetScopePanel`'s own shape -------

  it('renders the enabled state and shows no lifecycle control without graph:edit', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-state"]')?.textContent).toContain('enabled');
    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-routine-panel-enable"]')).toBeNull();
  });

  it('marks a retired routine distinctly', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true, renderedRetired: true } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-state"]')?.textContent).toContain('retired');
  });

  it('shows Retire for an identity with graph:edit, and Re-enable once retired', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-panel-enable"]')).toBeNull();

    fixture.componentRef.setInput('vm', { ...VM, retired: true, renderedRetired: true });
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="gardening-routine-panel-enable"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeNull();
  });

  it('disables the lifecycle control while its mutation is pending', async () => {
    const fixture = await mount({ canEdit: true, lifecyclePending: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-retire"]')?.disabled).toBe(
      true,
    );
  });

  it('emits retire once the operator confirms', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | null = null;
    fixture.componentInstance.retire.subscribe((slug) => (emitted = slug));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe('nightly');
  });

  it('emits nothing when the operator cancels the retire confirm', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted = false;
    fixture.componentInstance.retire.subscribe(() => (emitted = true));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-cancel"]')?.click();

    expect(emitted).toBe(false);
  });

  it('emits enable once the operator confirms', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true, renderedRetired: true }, canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted = false;
    fixture.componentInstance.enable.subscribe(() => (emitted = true));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-enable"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe(true);
  });

  it('renders a lifecycle action error', async () => {
    const fixture = await mount({ canEdit: true, actionError: 'Retire failed.' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-error"]')?.textContent).toContain(
      'Retire failed.',
    );
  });

  it('hides Run and shows a retired notice while renderedRetired, even though not blocked', async () => {
    const fixture = await mount({ vm: { ...VM, retired: true, renderedRetired: true } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-run"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-routine-retired-notice"]')?.textContent).toContain('Retired');
  });

  it('keeps the control choice keyed off the real retired, not the rendered override', async () => {
    // The real state is enabled; only an in-flight retire's own predicted outcome
    // is retired. The control offered must still be Retire, not Re-enable, so a
    // second click cannot fire the wrong verb (`scope-panel.spec.ts`'s own
    // "renders the retired badge from renderedRetired" pairing).
    const fixture = await mount({ vm: { ...VM, retired: false, renderedRetired: true }, canEdit: true });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-panel-state"]')?.textContent).toContain('retired');
    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="gardening-routine-panel-enable"]')).toBeNull();
  });

  it('shows Edit in the header only for an identity with graph:edit', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-routine-panel-edit"]')).toBeNull();

    fixture.componentRef.setInput('canEdit', true);
    fixture.componentRef.setInput('editable', true);
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-routine-record"] [data-testid="gardening-routine-panel-edit"]')).toBeTruthy();
  });

  const editTree = (draft: Partial<RoutineEditDraft> = {}) =>
    TestBed.runInInjectionContext(() => form(signal<RoutineEditDraft>({ graph: 'garden-routine', urgency: '', ...draft }), routineEditRules));

  it('offers no Edit control, and shows the urgency read-only, for a routine that is not editable', async () => {
    const fixture = await mount({ canEdit: true, editable: false });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="gardening-routine-panel-edit"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-routine-urgency-select"]')).toBeNull();
    expect(el.querySelector('[data-testid="gardening-routine-panel-retire"]')).toBeTruthy();
  });

  it('asks to open the graph when Edit is pressed, and shows no field until a form is handed in', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let started = 0;
    fixture.componentInstance.startEditGraph.subscribe(() => started++);

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-edit"]')!.click();

    expect(started).toBe(1);
    expect(el.querySelector('[data-testid="gardening-routine-panel-graph-input"]')).toBeNull();
  });

  it('renders the graph field over the handed tree and emits save and cancel', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    const tree = editTree();
    fixture.componentRef.setInput('editForm', tree);
    fixture.componentRef.setInput('editField', 'graph');
    await fixture.whenStable();
    let saves = 0;
    let cancels = 0;
    fixture.componentInstance.saveEdit.subscribe(() => saves++);
    fixture.componentInstance.cancelEdit.subscribe(() => cancels++);

    const input = el.querySelector<HTMLInputElement>('[data-testid="gardening-routine-panel-graph-input"]')!;
    expect(input.value).toBe('garden-routine');
    input.value = 'garden-delta';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await fixture.whenStable();
    expect(tree().value().graph).toBe('garden-delta');

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-graph-save"]')!.click();
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-graph-cancel"]')!.click();
    expect([saves, cancels]).toEqual([1, 1]);
    expect(el.querySelector('[data-testid="gardening-routine-panel-edit"]')?.hasAttribute('disabled')).toBe(true);
  });

  it('shows the required-graph error on the field once it is touched', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    const tree = editTree({ graph: '' });
    fixture.componentRef.setInput('editForm', tree);
    fixture.componentRef.setInput('editField', 'graph');
    tree.graph().markAsTouched();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="gardening-routine-panel-graph"]')?.textContent?.toLowerCase()).toContain('required');
  });

  it('shows a form-level refusal on the form line', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    const tree = editTree();
    fixture.componentRef.setInput('editForm', tree);
    fixture.componentRef.setInput('editField', 'graph');
    await fixture.whenStable();
    const { submit } = await import('@angular/forms/signals');
    await submit(tree, async () => [{ kind: 'hub', fieldTree: tree, message: 'the routine changed since it was read' }]);
    tree().markAsTouched();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="gardening-routine-panel-graph-line"]')?.textContent).toContain('changed since it was read');
  });

  it('renders read-only urgency as a labelled fact', async () => {
    const fixture = await mount({});
    const fact = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="gardening-routine-urgency"]')!;
    expect(fact.tagName.toLowerCase()).toBe('dl');
    expect(fact.querySelector('dt')?.textContent?.trim()).toBe('Default urgency');
    expect(fact.querySelector('dd')?.textContent?.trim()).toBe('Tenant default');
    expect(fact.querySelector('fleet-kit-select')).toBeNull();
  });

  it('opens the urgency edit on the chosen urgency, and renders it as a form once handed one', async () => {
    const fixture = await mount({ canEdit: true });
    fixture.componentRef.setInput('urgenciesReady', true);
    fixture.componentRef.setInput('urgencies', [
      { urgency_id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as UrgencyView,
      { urgency_id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as UrgencyView,
    ]);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const values: string[] = [];
    fixture.componentInstance.startEditUrgency.subscribe((v) => values.push(v));
    const trigger = root.querySelector<HTMLElement>('[data-testid="gardening-routine-urgency-select"]')!;
    expect(trigger.textContent).toContain('Tenant default (Normal)');
    trigger.click();
    await fixture.whenStable();
    [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent?.trim() === 'Urgent')!.click();
    await fixture.whenStable();
    expect(values).toEqual(['urgent']);

    fixture.componentRef.setInput('editForm', editTree({ urgency: 'urgent' }));
    fixture.componentRef.setInput('editField', 'urgency');
    await fixture.whenStable();
    expect(root.querySelector('[data-testid="gardening-routine-urgency-edit-save"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="gardening-routine-urgency-select"]')?.textContent).toContain('Urgent');
    fixture.destroy();
  });

  it.each([
    ['old', ['Tenant default (Normal)', 'Normal', 'Old']],
    [null, ['Tenant default (Normal)', 'Normal']],
  ])('offers a retired urgency only while it is the routine default (%s)', async (current, expected) => {
    const fixture = await mount({ canEdit: true });
    fixture.componentRef.setInput('urgenciesReady', true);
    fixture.componentRef.setInput('urgencies', [
      { urgency_id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as UrgencyView,
      { urgency_id: 'o', slug: 'old', name: 'Old', default: false, retired: true } as UrgencyView,
    ]);
    fixture.componentRef.setInput('defaultUrgency', current);
    await fixture.whenStable();
    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="gardening-routine-urgency-select"]')!.click();
    await fixture.whenStable();
    expect([...document.body.querySelectorAll<HTMLElement>('[role="option"]')].map((o) => o.textContent?.trim())).toEqual(expected);
    fixture.destroy();
  });

});
