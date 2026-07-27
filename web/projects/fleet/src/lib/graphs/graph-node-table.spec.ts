import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { GraphNodeView } from '../api/hub';
import { GraphNodeTable } from './graph-node-table';

const NODES: readonly GraphNodeView[] = [
  {
    node_id: 'n_build',
    name: 'build',
    executor: 'claude',
    session: 'fresh',
    judged_by: 'reviewer',
    mode: 'edit',
    checks: ['lint', 'test'],
    produces: [{ name: 'branch' }],
    retries_max: 3,
    retries_exhausted: 'escalate',
    choices: [],
  },
  {
    node_id: 'n_review',
    name: 'review',
    executor: 'claude',
    session: 'fresh',
    judged_by: 'reviewer',
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
    expect(buildRow.textContent).toContain('claude');
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
      { ...NODES[0], node_id: 'n_targeted', session: 'resume', session_source: 'code' },
      { ...NODES[0], node_id: 'n_bare', session: 'resume' },
      { ...NODES[0], node_id: 'n_fresh', session: 'fresh' },
    ] satisfies readonly GraphNodeView[]);
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
