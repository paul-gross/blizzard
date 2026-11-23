import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { Graph, GraphNode } from './graph.domain';
import { GraphDiagramView } from './graph-diagram-view';

const GRAPH_DEFAULTS: Pick<Graph, 'edges' | 'retired' | 'sessions'> = {
  edges: [],
  retired: false,
  sessions: [],
};

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

const EDGE_DEFAULTS: Pick<Graph['edges'][number], 'promptAddendum' | 'targetGraph' | 'targetKind'> = {
  promptAddendum: null,
  targetGraph: null,
  targetKind: 'node',
};

const GRAPH_A: Graph = {
  ...GRAPH_DEFAULTS,
  graphId: 'gr_a',
  name: 'a',
  entryNodeId: 'n_build',
  nodes: [
    { ...NODE_DEFAULTS, nodeId: 'n_build', name: 'build', executor: 'runner', session: 'fresh', judgedBy: 'worker', choices: [{ choiceId: 'c_pass', name: 'pass', description: '' }] },
    { ...NODE_DEFAULTS, nodeId: 'n_review', name: 'review', executor: 'runner', session: 'fresh', judgedBy: 'worker', choices: [] },
  ],
  edges: [{ ...EDGE_DEFAULTS, fromNodeId: 'n_build', choiceId: 'c_pass', toNodeName: 'review' }],
};

const GRAPH_B: Graph = {
  ...GRAPH_DEFAULTS,
  graphId: 'gr_b',
  name: 'b',
  entryNodeId: 'n_only',
  nodes: [{ ...NODE_DEFAULTS, nodeId: 'n_only', name: 'only', executor: 'runner', session: 'fresh', judgedBy: 'worker', choices: [] }],
  edges: [],
};

function mount(graph: Graph) {
  TestBed.configureTestingModule({
    imports: [GraphDiagramView],
    providers: [provideZonelessChangeDetection()],
  });
  const fixture = TestBed.createComponent(GraphDiagramView);
  fixture.componentRef.setInput('graph', graph);
  fixture.detectChanges();
  return fixture;
}

describe('GraphDiagramView', () => {
  it('routes a diagram click into the detail pane', () => {
    const fixture = mount(GRAPH_A);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-diagram-detail-empty"]')).toBeTruthy();

    const buildNode = el.querySelector('[data-testid="graph-diagram-node"][data-node-id="n_build"]') as HTMLElement;
    buildNode.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    fixture.detectChanges();

    expect(el.querySelector('[data-testid="graph-diagram-detail-node"]')?.textContent).toContain('build');
    expect(buildNode.getAttribute('data-selected')).toBe('true');
  });

  it('clears both the diagram highlight and the pane on an empty-canvas click', () => {
    const fixture = mount(GRAPH_A);
    const el = fixture.nativeElement as HTMLElement;

    const buildNode = el.querySelector('[data-testid="graph-diagram-node"][data-node-id="n_build"]') as HTMLElement;
    buildNode.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    fixture.detectChanges();
    expect(el.querySelector('[data-testid="graph-diagram-detail-node"]')).toBeTruthy();

    const svg = el.querySelector('[data-testid="graph-diagram-svg"]') as HTMLElement;
    svg.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    fixture.detectChanges();

    expect(el.querySelector('[data-testid="graph-diagram-detail-empty"]')).toBeTruthy();
    expect(buildNode.getAttribute('data-selected')).toBeNull();
  });

  it('clears the selection when the graph input changes', () => {
    const fixture = mount(GRAPH_A);
    const el = fixture.nativeElement as HTMLElement;

    const buildNode = el.querySelector('[data-testid="graph-diagram-node"][data-node-id="n_build"]') as HTMLElement;
    buildNode.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    fixture.detectChanges();
    expect(el.querySelector('[data-testid="graph-diagram-detail-node"]')).toBeTruthy();

    fixture.componentRef.setInput('graph', GRAPH_B);
    fixture.detectChanges();

    expect(el.querySelector('[data-testid="graph-diagram-detail-empty"]')).toBeTruthy();
  });
});
