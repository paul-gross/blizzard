import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { GraphView } from '../api/hub';
import { GraphDiagramView } from './graph-diagram-view';

const GRAPH_A: GraphView = {
  graph_id: 'gr_a',
  name: 'a',
  enabled: true,
  entry_node_id: 'n_build',
  nodes: [
    { node_id: 'n_build', name: 'build', executor: 'runner', session: 'fresh', judged_by: 'worker', choices: [{ choice_id: 'c_pass', name: 'pass', description: '' }] },
    { node_id: 'n_review', name: 'review', executor: 'runner', session: 'fresh', judged_by: 'worker', choices: [] },
  ],
  edges: [{ from_node_id: 'n_build', choice_id: 'c_pass', to_node_name: 'review' }],
  warnings: [],
};

const GRAPH_B: GraphView = {
  graph_id: 'gr_b',
  name: 'b',
  enabled: true,
  entry_node_id: 'n_only',
  nodes: [{ node_id: 'n_only', name: 'only', executor: 'runner', session: 'fresh', judged_by: 'worker', choices: [] }],
  edges: [],
  warnings: [],
};

function mount(graph: GraphView) {
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
