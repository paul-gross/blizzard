import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetRoutinePanel, type RoutinePanelVm } from './routine-panel';

const VM: RoutinePanelVm = {
  record: {
    name: 'nightly',
    graphName: 'garden-routine',
    defaultScopeSlug: 'blizzard',
    defaultModel: ['claude-sonnet-5'],
    defaultEffort: 'medium',
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
};

describe('FleetRoutinePanel', () => {
  async function mount(inputs: { vm?: RoutinePanelVm | null; state?: 'loading' | 'error' | 'empty' | 'ready' }) {
    await TestBed.configureTestingModule({
      imports: [FleetRoutinePanel],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetRoutinePanel);
    // `'vm' in inputs`, not `??`: an explicit `null` is the nothing-selected case
    // this panel has its own rendering for, and must not fall back to `VM`.
    fixture.componentRef.setInput('vm', 'vm' in inputs ? inputs.vm : VM);
    fixture.componentRef.setInput('state', inputs.state ?? 'ready');
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

  it('renders the record as a fact list with the same four rows, name/graph/scope/model/effort', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const record = el.querySelector('[data-testid="gardening-routine-record"]');
    expect(record?.textContent).toContain('nightly');
    const facts = el.querySelector('[data-testid="gardening-routine-record-facts"]');
    expect(facts?.tagName.toLowerCase()).toBe('dl');
    expect(facts?.textContent).toContain('garden-routine');
    expect(facts?.textContent).toContain('blizzard');
    expect(facts?.textContent).toContain('claude-sonnet-5');
    expect(facts?.textContent).toContain('medium');
  });

  it('falls back to an em dash for an empty default model and effort', async () => {
    const fixture = await mount({
      vm: { ...VM, record: { ...VM.record, defaultModel: [], defaultEffort: null } },
    });
    const el = fixture.nativeElement as HTMLElement;

    const facts = el.querySelector('[data-testid="gardening-routine-record-facts"]');
    expect(facts?.textContent).toContain('—');
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

  it('offers a cta-sized Run button alone, naming no CLI invocation, and emits run on click', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    const actions = el.querySelector('[data-testid="gardening-routine-actions"]');
    const button = actions?.querySelector('[data-testid="gardening-routine-run"]');
    expect(button).toBeTruthy();
    expect(button?.classList.contains('cta')).toBe(true);
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

  it('names no CLI read verb anywhere in the panel', async () => {
    const fixture = await mount({});
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('hub routine show');
    expect(el.textContent).not.toContain('hub routine trend');
    expect(el.textContent).not.toContain('hub routine sweeps');
  });
});
