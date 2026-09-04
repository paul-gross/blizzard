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
      node_id: 'n_plan',
      name: 'plan',
      executor: 'runner',
      session: 'fresh',
      judged_by: 'none',
      choices: [{ choice_id: 'c_advance', name: 'advance', description: '' }],
    },
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
  edges: [
    { from_node_id: 'n_plan', choice_id: 'c_advance', to_node_name: 'build', prompt_addendum: 'Arriving fresh from plan.' },
    { from_node_id: 'n_build', choice_id: 'c_pass', to_node_name: 'deliver', prompt_addendum: 'Watch for flaky tests.' },
  ],
  warnings: [],
};

function mount(graph: GraphView, selection: DiagramSelection | null) {
  TestBed.configureTestingModule({
    imports: [GraphDiagramDetail],
    providers: [provideZonelessChangeDetection()],
  });
  const fixture = TestBed.createComponent(GraphDiagramDetail);
  fixture.componentRef.setInput('graph', graph);
  fixture.componentRef.setInput('selection', selection);
  fixture.detectChanges();
  return fixture;
}

describe('GraphDiagramDetail', () => {
  it('renders a neutral hint when nothing is selected', () => {
    const fixture = mount(GRAPH, null);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-empty"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-diagram-detail-node"]')).toBeNull();
    expect(el.querySelector('[data-testid="graph-diagram-detail-edge"]')).toBeNull();
  });

  it("renders a selected node's fields through the kit fact list, including its targeted resume in resume:<node> form (the shared helper)", () => {
    const fixture = mount(GRAPH, { kind: 'node', nodeId: 'n_build' });
    const el = fixture.nativeElement as HTMLElement;
    const node = el.querySelector('[data-testid="graph-diagram-detail-node"]') as HTMLElement;
    expect(node).toBeTruthy();
    expect(node.querySelector('[data-testid="graph-diagram-detail-node-facts"]')).toBeTruthy();

    const text = node.textContent ?? '';
    expect(text).toContain('build');
    expect(text).toContain('runner');
    expect(text).toContain('resume:code');
    expect(text).toContain('worker');
    expect(text).toContain('2 → fail');
    expect(text).toContain('lint, test');
    expect(text).toContain('plan, retrospective');
  });

  it("renders a selected node's prompt and judgement_prompt text in full, through the kit prose block", () => {
    const fixture = mount(GRAPH, { kind: 'node', nodeId: 'n_build' });
    const el = fixture.nativeElement as HTMLElement;
    // `testid` marks the prose block's own outer element, which also carries its
    // `label` — so this checks the text is present, not that it's the element's
    // sole content (`finding-panel.spec.ts`'s own `fp-summary`/`fp-note` convention).
    expect(el.querySelector('[data-testid="graph-diagram-detail-prompt"]')?.textContent).toContain('Build the thing.\nDo it well.');
    expect(el.querySelector('[data-testid="graph-diagram-detail-judgement-prompt"]')?.textContent).toContain('Did it pass?');
  });

  it('omits the prompt/judgement_prompt blocks for a node with neither', () => {
    const fixture = mount(GRAPH, { kind: 'node', nodeId: 'n_deliver' });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-prompt"]')).toBeNull();
    expect(el.querySelector('[data-testid="graph-diagram-detail-judgement-prompt"]')).toBeNull();
  });

  it("lists every inbound edge's prompt addendum below the node's own prompt, labelled by source node and choice", () => {
    const fixture = mount(GRAPH, { kind: 'node', nodeId: 'n_build' });
    const el = fixture.nativeElement as HTMLElement;
    const group = el.querySelector('[data-testid="graph-diagram-detail-incoming-addenda"]') as HTMLElement;
    expect(group).toBeTruthy();

    const entries = group.querySelectorAll('[data-testid="graph-diagram-detail-incoming-addendum"]');
    expect(entries.length).toBe(1);
    expect(entries[0].textContent).toContain('Arriving from plan · advance');
    expect(entries[0].textContent).toContain('Arriving fresh from plan.');
  });

  it('omits the inbound-addenda group for a node with no inbound edges carrying one', () => {
    const fixture = mount(GRAPH, { kind: 'node', nodeId: 'n_plan' });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-incoming-addenda"]')).toBeNull();
  });

  it('lists every distinct inbound route when a node has more than one', () => {
    const graph: GraphView = {
      ...GRAPH,
      edges: [
        ...GRAPH.edges!,
        { from_node_id: 'n_plan', choice_id: 'c_advance', to_node_name: 'build', prompt_addendum: 'A second route in.' },
      ],
    };
    const fixture = mount(graph, { kind: 'node', nodeId: 'n_build' });
    const el = fixture.nativeElement as HTMLElement;
    const entries = el.querySelectorAll('[data-testid="graph-diagram-detail-incoming-addendum"]');
    expect(entries.length).toBe(2);
  });

  it("renders a selected edge's choice name, source, target, kind, and description through the kit fact list", () => {
    const fixture = mount(GRAPH, {
      kind: 'edge',
      edgeId: 'e0',
      fromNodeId: 'n_build',
      target: { kind: 'node', nodeId: 'n_deliver' },
      choiceId: 'c_pass',
      edgeKind: 'advance',
    });
    const el = fixture.nativeElement as HTMLElement;
    const edge = el.querySelector('[data-testid="graph-diagram-detail-edge"]') as HTMLElement;
    expect(edge).toBeTruthy();
    expect(edge.querySelector('[data-testid="graph-diagram-detail-edge-facts"]')).toBeTruthy();

    const text = edge.textContent ?? '';
    expect(text).toContain('pass');
    expect(text).toContain('build');
    expect(text).toContain('deliver');
    expect(text).toContain('advance');
    // The description is a prose block now, so its own "Description" label rides in
    // the same element's text.
    expect(el.querySelector('[data-testid="graph-diagram-detail-choice-description"]')?.textContent).toContain(
      'moves on to review',
    );
    expect(el.querySelector('[data-testid="graph-diagram-detail-prompt-addendum"]')?.textContent).toContain(
      'Watch for flaky tests.',
    );
  });

  it('renders "done" as the target for an edge into the reserved done terminal', () => {
    const fixture = mount(GRAPH, {
      kind: 'edge',
      edgeId: 'e1',
      fromNodeId: 'n_deliver',
      target: { kind: 'done' },
      choiceId: 'c_landed',
      edgeKind: 'advance',
    });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-target"]')?.textContent?.trim()).toBe('done');
  });

  it('renders the target graph name for a migration edge', () => {
    const fixture = mount(GRAPH, {
      kind: 'edge',
      edgeId: 'e2',
      fromNodeId: 'n_build',
      target: { kind: 'graph', targetGraph: 'default-delivery' },
      choiceId: 'c_fail',
      edgeKind: 'advance',
    });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-target"]')?.textContent?.trim()).toBe('default-delivery');
  });

  it('omits the choice-description and prompt-addendum blocks when the edge has neither', () => {
    const fixture = mount(GRAPH, {
      kind: 'edge',
      edgeId: 'e1',
      fromNodeId: 'n_build',
      target: { kind: 'node', nodeId: 'n_build' },
      choiceId: 'c_fail',
      edgeKind: 'retry',
    });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="graph-diagram-detail-choice-description"]')).toBeNull();
    expect(el.querySelector('[data-testid="graph-diagram-detail-prompt-addendum"]')).toBeNull();
  });
});
