import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { GraphView } from '../api/hub';
import { GraphDiagramDetail } from './graph-diagram-detail';
import type { DiagramSelection } from './graph-diagram-selection';

const GRAPH: GraphView = {
  graph_id: 'gr_test',
  name: 'test',
  enabled: true,
  entry_node_id: 'n_build',
  nodes: [
    {
      node_id: 'n_build',
      name: 'build',
      executor: 'runner',
      session: 'resume',
      session_source: 'code',
      judged_by: 'worker',
      mode: null,
      retries_max: 2,
      retries_exhausted: 'fail',
      checks: ['lint', 'test'],
      produces: [{ name: 'plan' }, { name: 'retrospective' }],
      prompt: 'Build the thing.\nDo it well.',
      judgement_prompt: 'Did it pass?',
      choices: [
        { choice_id: 'c_pass', name: 'pass', description: 'moves on to review' },
        { choice_id: 'c_fail', name: 'fail', description: '' },
      ],
    },
    {
      node_id: 'n_deliver',
      name: 'deliver',
      executor: 'hub',
      session: 'fresh',
      judged_by: 'none',
      mode: 'merge-to-main',
      choices: [{ choice_id: 'c_landed', name: 'landed', description: '' }],
    },
  ],
  edges: [],
  warnings: [],
};

function mount(selection: DiagramSelection | null) {
  TestBed.configureTestingModule({
    imports: [GraphDiagramDetail],
    providers: [provideZonelessChangeDetection()],
  });
  const fixture = TestBed.createComponent(GraphDiagramDetail);
  fixture.componentRef.setInput('graph', GRAPH);
  fixture.componentRef.setInput('selection', selection);
  fixture.detectChanges();
  return fixture;
}

describe('GraphDiagramDetail', () => {
  it('renders a neutral hint when nothing is selected', () => {
    const fixture = mount(null);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-diagram-detail-node"]')).toBeNull();
    expect(el.querySelector('[data-testid="graph-diagram-detail-edge"]')).toBeNull();
  });

  it("renders a selected node's fields, including its targeted resume in resume:<node> form (the shared helper)", () => {
    const fixture = mount({ kind: 'node', nodeId: 'n_build' });
    const el = fixture.nativeElement as HTMLElement;
    const node = el.querySelector('[data-testid="graph-diagram-detail-node"]') as HTMLElement;
    expect(node).toBeTruthy();

    const text = node.textContent ?? '';
    expect(text).toContain('build');
    expect(text).toContain('runner');
    expect(text).toContain('resume:code');
    expect(text).toContain('worker');
    expect(text).toContain('2 → fail');
    expect(text).toContain('lint, test');
    expect(text).toContain('plan, retrospective');
  });

  it("renders a selected node's prompt and judgement_prompt text in full", () => {
    const fixture = mount({ kind: 'node', nodeId: 'n_build' });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-prompt"]')?.textContent).toBe('Build the thing.\nDo it well.');
    expect(el.querySelector('[data-testid="graph-diagram-detail-judgement-prompt"]')?.textContent).toBe('Did it pass?');
  });

  it('omits the prompt/judgement_prompt blocks for a node with neither', () => {
    const fixture = mount({ kind: 'node', nodeId: 'n_deliver' });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-prompt"]')).toBeNull();
    expect(el.querySelector('[data-testid="graph-diagram-detail-judgement-prompt"]')).toBeNull();
  });

  it("renders a selected edge's choice name, source, target, kind, and description", () => {
    const fixture = mount({
      kind: 'edge',
      edgeId: 'e0',
      fromNodeId: 'n_build',
      toNodeId: 'n_deliver',
      choiceId: 'c_pass',
      edgeKind: 'advance',
    });
    const el = fixture.nativeElement as HTMLElement;
    const edge = el.querySelector('[data-testid="graph-diagram-detail-edge"]') as HTMLElement;
    expect(edge).toBeTruthy();

    const text = edge.textContent ?? '';
    expect(text).toContain('pass');
    expect(text).toContain('build');
    expect(text).toContain('deliver');
    expect(text).toContain('advance');
    expect(el.querySelector('[data-testid="graph-diagram-detail-choice-description"]')?.textContent?.trim()).toBe(
      'moves on to review',
    );
  });

  it('renders "done" as the target for an edge into the reserved done terminal', () => {
    const fixture = mount({
      kind: 'edge',
      edgeId: 'e1',
      fromNodeId: 'n_deliver',
      toNodeId: null,
      choiceId: 'c_landed',
      edgeKind: 'advance',
    });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-target"]')?.textContent?.trim()).toBe('done');
  });

  it('omits the choice-description paragraph when the choice has none', () => {
    const fixture = mount({
      kind: 'edge',
      edgeId: 'e1',
      fromNodeId: 'n_build',
      toNodeId: 'n_build',
      choiceId: 'c_fail',
      edgeKind: 'retry',
    });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-choice-description"]')).toBeNull();
  });
});
