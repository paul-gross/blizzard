import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { BoardDecisionPresentation } from './gates.domain';
import { GatesPanelView } from './gates-view';

const GATES: BoardDecisionPresentation[] = [
  {
    decision_id: 'dc_01',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    node_name: 'approve-gate',
    imposed_by_runner_id: null,
    imposed_by_runner_name: null,
    choices: ['approve', 'reject'],
  },
  {
    decision_id: 'dc_02',
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YAB',
    node_name: 'deploy-gate',
    imposed_by_runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3',
    imposed_by_runner_name: 'r-claude',
    choices: [],
  },
];

describe('GatesPanelView', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GatesPanelView],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  const render = async (state: string, gates: readonly BoardDecisionPresentation[]) => {
    const fixture = TestBed.createComponent(GatesPanelView);
    fixture.componentRef.setInput('state', state);
    fixture.componentRef.setInput('gates', gates);
    await fixture.whenStable();
    return fixture;
  };

  it('lists every gate it is handed with its chunk, node, and choices — off plain inputs alone', async () => {
    const fixture = await render('ready', GATES);
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="rail-gate"]');
    expect(rows).toHaveLength(2);
    expect(el.querySelector('[data-testid="gates-count"]')?.textContent).toContain('2');
    expect(rows[0].querySelector('[data-testid="rail-gate-chunk"]')?.textContent).toContain('C-3YJ9');
    expect(rows[0].querySelector('[data-testid="rail-gate-node"]')?.textContent).toContain('approve-gate');
    expect(rows[0].querySelector('[data-testid="rail-gate-choices"]')?.textContent).toContain('approve · reject');
  });

  it('names each row after its chunk and the gate it resolves', async () => {
    const fixture = await render('ready', GATES);
    const row = (fixture.nativeElement as HTMLElement).querySelector(
      '[data-testid="rail-gate"]',
    );

    expect(row?.getAttribute('aria-label')).toBe(
      'Open chunk C-3YJ9 to resolve its gate',
    );
  });

  it('names the origin: the graph, or the runner that imposed the gate', async () => {
    const fixture = await render('ready', GATES);
    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('[data-testid="rail-gate"]');

    expect(rows[0].querySelector('[data-testid="rail-gate-origin"]')?.textContent?.trim()).toBe('graph');
    expect(rows[1].querySelector('[data-testid="rail-gate-origin"]')?.textContent?.trim()).toBe('runner R-ABF3.r-claude');
  });

  it('omits the choices line for a gate that offers none', async () => {
    const fixture = await render('ready', GATES);
    const second = (fixture.nativeElement as HTMLElement).querySelectorAll('[data-testid="rail-gate"]')[1];

    expect(second.querySelector('[data-testid="rail-gate-choices"]')).toBeNull();
  });

  it('emits selectChunk when a gate is activated', async () => {
    const fixture = await render('ready', GATES);
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));

    (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLElement>('[data-testid="rail-gate"]')
      ?.click();
    await fixture.whenStable();
    expect(selected).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });

  it('opens the focused gate on Enter, in a rail with no selected row', async () => {
    const fixture = await render('ready', GATES);
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    const el = fixture.nativeElement as HTMLElement;
    const row = el.querySelector<HTMLElement>('[data-testid="rail-gate"]')!;

    expect(
      el.querySelector('[role="listbox"]')?.getAttribute('aria-label'),
    ).toBe('Open gates');
    expect(
      el.querySelector('[role="option"][aria-selected="true"]'),
    ).toBeNull();
    row.focus();
    row.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
    );
    await fixture.whenStable();
    expect(selected).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
  });

  it('rests on an empty state with no gates once loaded', async () => {
    const fixture = await render('empty', []);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gates-empty"]')?.textContent).toContain('NO OPEN GATES');
    expect(el.querySelector('[data-testid="gates-count"]')).toBeNull();
  });

  it('withholds the empty copy while the decisions read is pending', async () => {
    const fixture = await render('loading', []);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gates-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="gates-empty"]')).toBeNull();
  });

  it('shows an error state when the decisions read fails', async () => {
    const fixture = await render('error', []);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="gates-error"]')?.textContent).toContain('FAILED TO LOAD GATES');
  });
});
