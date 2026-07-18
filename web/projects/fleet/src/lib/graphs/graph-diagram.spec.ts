import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { GraphView } from '../api/hub';
import { GRAPH_LAYOUT, GRAPH_TEXT_MEASURER, GraphDiagram } from './graph-diagram';
import type { LaidOutGraph, LayoutOutcome } from './graph-layout';

const GRAPH: GraphView = {
  graph_id: 'gr_build_v2',
  name: 'build',
  enabled: true,
  entry_node_id: 'n_build',
  nodes: [{ node_id: 'n_build', name: 'build', executor: 'runner', session: 'fresh', judged_by: 'worker', choices: [] }],
  edges: [],
  warnings: [],
};

const LAID_OUT: LaidOutGraph = {
  width: 320,
  height: 200,
  nodes: [
    {
      id: 'n_build',
      name: 'build',
      executor: 'runner',
      metaText: 'resume',
      isEntry: true,
      x: 20,
      y: 20,
      width: 150,
      height: 60,
    },
    {
      id: 'n_deliver',
      name: 'deliver',
      executor: 'hub',
      metaText: '',
      isEntry: false,
      x: 20,
      y: 120,
      width: 150,
      height: 60,
    },
  ],
  edges: [
    {
      id: 'e0',
      kind: 'advance',
      path: 'M 95 80 L 95 120',
      label: { text: 'pass', x: 95, y: 100, width: 40, height: 20 },
    },
  ],
  selfLoops: [
    {
      nodeId: 'n_build',
      path: 'M 170 8 C 214 -2, 214 32, 174 20',
      label: { text: 'fail', x: 216, y: 20, width: 36, height: 20 },
    },
  ],
  done: { x: 95, y: 220, r: 24 },
};

function mount(outcome: LayoutOutcome) {
  TestBed.configureTestingModule({
    imports: [GraphDiagram],
    providers: [
      provideZonelessChangeDetection(),
      { provide: GRAPH_LAYOUT, useValue: () => outcome },
      { provide: GRAPH_TEXT_MEASURER, useValue: (text: string) => text.length * 7 },
    ],
  });
  const fixture = TestBed.createComponent(GraphDiagram);
  fixture.componentRef.setInput('graph', GRAPH);
  fixture.detectChanges();
  return fixture;
}

describe('GraphDiagram', () => {
  it('renders nodes, edges, self-loop, labels, and the done sink from a stubbed layout', () => {
    const fixture = mount({ ok: true, graph: LAID_OUT });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-diagram-fallback"]')).toBeNull();
    expect(el.querySelector('[data-testid="graph-diagram-svg"]')).toBeTruthy();

    const nodes = el.querySelectorAll('[data-testid="graph-diagram-node"]');
    expect(nodes).toHaveLength(2);
    expect(nodes[0].getAttribute('data-node-id')).toBe('n_build');
    expect(nodes[0].querySelector('[data-testid="graph-diagram-entry-ring"]')).toBeTruthy();
    expect(nodes[0].querySelector('[data-testid="graph-diagram-node-name"]')?.textContent?.trim()).toBe('build');
    expect(nodes[0].querySelector('[data-testid="graph-diagram-node-badge"]')?.textContent?.trim()).toBe('RUNNER');
    expect(nodes[1].querySelector('[data-testid="graph-diagram-entry-ring"]')).toBeNull();

    const edges = el.querySelectorAll('[data-testid="graph-diagram-edge"]');
    expect(edges).toHaveLength(1);
    expect(edges[0].getAttribute('data-edge-kind')).toBe('advance');

    const labels = el.querySelectorAll('[data-testid="graph-diagram-edge-label"]');
    expect(Array.from(labels).map((l) => l.textContent?.trim())).toEqual(['pass', 'fail']);

    const selfLoop = el.querySelector('[data-testid="graph-diagram-self-loop"]');
    expect(selfLoop?.getAttribute('data-node-id')).toBe('n_build');

    expect(el.querySelector('[data-testid="graph-diagram-done"]')).toBeTruthy();
  });

  it('shows an unobtrusive fallback notice and no diagram when layout fails, without throwing', () => {
    const fixture = mount({ ok: false });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-diagram-fallback"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-diagram-svg"]')).toBeNull();
  });

  it('renders a node with no meta line without a meta text element', () => {
    const fixture = mount({ ok: true, graph: LAID_OUT });
    const el = fixture.nativeElement as HTMLElement;
    const deliverNode = el.querySelector('[data-node-id="n_deliver"]') as HTMLElement;
    expect(deliverNode.querySelector('.node-meta')).toBeNull();
  });
});
