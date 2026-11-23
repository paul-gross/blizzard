import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { GraphNode } from './graph.domain';
import { GraphNodeTable } from './graph-node-table';

const NODE_DEFAULTS: Pick<GraphNode, 'action' | 'actionArgs' | 'checks' | 'choices' | 'judgementPrompt' | 'produces' | 'prompt' | 'retriesExhausted' | 'retriesMax' | 'sessionSource'> = {
  action: null,
  actionArgs: {},
  checks: [],
  choices: [],
  judgementPrompt: null,
  produces: [],
  prompt: null,
  retriesExhausted: null,
  retriesMax: null,
  sessionSource: null,
};

const NODES: readonly GraphNode[] = [
  {
    ...NODE_DEFAULTS,
    nodeId: 'n_build',
    name: 'build',
    executor: 'runner',
    session: 'fresh',
    judgedBy: 'worker',
    checks: ['lint', 'test'],
    produces: ['branch'],
    retriesMax: 3,
    retriesExhausted: 'escalate',
    choices: [],
  },
  {
    ...NODE_DEFAULTS,
    nodeId: 'n_review',
    name: 'review',
    executor: 'runner',
    session: 'fresh',
    judgedBy: 'worker',
    choices: [],
  },
];

describe('GraphNodeTable', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GraphNodeTable],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders one row per node, retries, checks, and produces names (kind dropped)', async () => {
    const fixture = TestBed.createComponent(GraphNodeTable);
    fixture.componentRef.setInput('nodes', NODES);
    fixture.componentRef.setInput('entryNodeId', 'n_build');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="graph-detail-node-row"]');
    expect(rows).toHaveLength(2);

    const buildRow = el.querySelector('[data-node-id="n_build"]') as HTMLElement;
    expect(buildRow.querySelector('[data-testid="graph-detail-entry-badge"]')).toBeTruthy();
    expect(buildRow.textContent).toContain('runner');
    expect(buildRow.textContent).toContain('3');
    expect(buildRow.textContent).toContain('escalate');
    expect(buildRow.textContent).toContain('lint, test');
    expect(buildRow.textContent).toContain('branch');

    const reviewRow = el.querySelector('[data-node-id="n_review"]') as HTMLElement;
    expect(reviewRow.querySelector('[data-testid="graph-detail-entry-badge"]')).toBeNull();
    expect(reviewRow.textContent).toContain('—');
  });

  it('renders the Session column in its authored form, targeted resumes included (issue #158)', async () => {
    const fixture = TestBed.createComponent(GraphNodeTable);
    fixture.componentRef.setInput('nodes', [
      { ...NODES[0], nodeId: 'n_targeted', session: 'resume', sessionSource: 'code' },
      { ...NODES[0], nodeId: 'n_bare', session: 'resume' },
      { ...NODES[0], nodeId: 'n_fresh', session: 'fresh' },
    ] satisfies readonly GraphNode[]);
    fixture.componentRef.setInput('entryNodeId', 'n_targeted');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const session = (nodeId: string) =>
      el.querySelector(`[data-node-id="${nodeId}"]`)?.querySelectorAll('td')[2]?.textContent?.trim();

    expect(session('n_targeted')).toBe('resume:code');
    expect(session('n_bare')).toBe('resume');
    expect(session('n_fresh')).toBe('fresh');
  });
});
