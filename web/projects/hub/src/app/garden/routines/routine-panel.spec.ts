import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetRoutinePanel, type RoutinePanelVm } from './routine-panel';

const VM: RoutinePanelVm = {
  record: {
    name: 'nightly',
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
};

describe('FleetRoutinePanel', () => {
  async function mount(inputs: {
    vm?: RoutinePanelVm | null;
    state?: 'loading' | 'error' | 'empty' | 'ready';
    canEdit?: boolean;
    actionError?: string | null;
    lifecyclePending?: boolean;
    editPending?: boolean;
  }) {
    await TestBed.configureTestingModule({
      imports: [FleetRoutinePanel],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetRoutinePanel);
    // `'vm' in inputs`, not `??`: an explicit `null` is the nothing-selected case
    // this panel has its own rendering for, and must not fall back to `VM`.
    fixture.componentRef.setInput('vm', 'vm' in inputs ? inputs.vm : VM);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
    fixture.componentRef.setInput('canEdit', inputs.canEdit ?? false);
    fixture.componentRef.setInput('actionError', inputs.actionError ?? null);
    fixture.componentRef.setInput('lifecyclePending', inputs.lifecyclePending ?? false);
    fixture.componentRef.setInput('editPending', inputs.editPending ?? false);
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
    expect(record?.textContent).toContain('nightly');
    const facts = el.querySelector('[data-testid="gardening-routine-record-facts"]');
    expect(facts?.tagName.toLowerCase()).toBe('dl');
    expect(Array.from(facts!.querySelectorAll('dt')).map((dt) => dt.textContent?.trim())).toEqual([
      'Graph',
      'Default scope',
      'Model · effort',
      'Cadence',
      'Runs',
    ]);
    expect(el.querySelector('[data-testid="gardening-routine-fact-graph"]')?.textContent).toContain(
      'garden-routine · tenant-wide library',
    );
    expect(el.querySelector('[data-testid="gardening-routine-fact-model"]')?.textContent).toContain(
      'claude-sonnet-5 · medium',
    );
    expect(el.querySelector('[data-testid="gardening-routine-fact-cadence"]')?.textContent).toContain('manual');
    expect(el.querySelector('[data-testid="gardening-routine-fact-runs"]')?.textContent).toContain('1 in the last 28 days');
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
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="gardening-routine-scopes"]')?.textContent).toContain(
      'No scope is related to this routine.',
    );
  });

  it('renders no related-scopes section while the relation read is still pending', async () => {
    const fixture = await mount({ vm: { ...VM, relatedScopes: null } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gardening-routine-scopes"]')).toBeNull();
  });

  it('names no CLI read verb anywhere in the panel', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('hub routine show');
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
    fixture.componentInstance.retire.subscribe((name) => (emitted = name));

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-retire"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(emitted).toBe(VM.record.name);
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
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-routine-record"] [data-testid="gardening-routine-panel-edit"]')).toBeTruthy();
  });

  it('opens the graph in place, emits the typed graph, and closes once the new graph reads back', async () => {
    const fixture = await mount({ canEdit: true });
    const el = fixture.nativeElement as HTMLElement;
    let emitted: string | null = null;
    fixture.componentInstance.editGraph.subscribe((g) => (emitted = g));
    expect(el.querySelector('[data-testid="gardening-routine-panel-graph-input"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-edit"]')!.click();
    await fixture.whenStable();
    const input = el.querySelector<HTMLInputElement>('[data-testid="gardening-routine-panel-graph-input"]')!;
    expect(input.value).toBe('garden-routine');
    input.value = 'garden-delta';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-graph-submit"]')!.click();

    expect(emitted).toBe('garden-delta');
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-routine-panel-graph-input"]'), 'open until it reads back').toBeTruthy();

    fixture.componentRef.setInput('vm', { ...VM, record: { ...VM.record, graphName: 'garden-delta' } });
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-routine-panel-graph-input"]')).toBeNull();
  });

  it('closes the editor on Cancel, and disables Set while the edit is pending', async () => {
    const fixture = await mount({ canEdit: true, editPending: true });
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-edit"]')!.click();
    await fixture.whenStable();
    expect(el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-graph-submit"]')?.disabled).toBe(true);

    el.querySelector<HTMLButtonElement>('[data-testid="gardening-routine-panel-graph-cancel"]')!.click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="gardening-routine-panel-graph-input"]')).toBeNull();
  });
});
