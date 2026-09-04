import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { GraphView } from '../api/hub';
import type { DiagramSelection } from './graph-diagram-selection';
import { GRAPH_LAYOUT, GraphDiagram } from './graph-diagram';
import type { LaidOutGraph, LayoutOutcome } from './graph-layout';
import { GRAPH_TEXT_MEASURER } from './graph-text-measurer';

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
      metaLines: ['resume:code · retries 2', '→ plan, retrospective'],
      x: 20,
      y: 20,
      width: 150,
      height: 75,
    },
    {
      id: 'n_deliver',
      name: 'deliver',
      executor: 'hub',
      metaLines: [],
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
      fromNodeId: 'n_build',
      target: { kind: 'node', nodeId: 'n_deliver' },
      choiceId: 'c_pass',
    },
  ],
  selfLoops: [
    {
      id: 'e1',
      nodeId: 'n_build',
      path: 'M 170 8 C 214 -2, 214 32, 174 20',
      label: { text: 'fail', x: 216, y: 20, width: 36, height: 20 },
      choiceId: 'c_fail',
    },
  ],
  done: { x: 95, y: 220, r: 24 },
  start: { x: 95, y: -20, r: 24, path: 'M 95 -20 L 95 20' },
  migrations: [],
};

function mount(outcome: LayoutOutcome, selection?: DiagramSelection | null) {
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
  if (selection !== undefined) fixture.componentRef.setInput('selection', selection);
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
    expect(nodes[0].querySelector('[data-testid="graph-diagram-node-name"]')?.textContent?.trim()).toBe('build');
    expect(nodes[0].querySelector('[data-testid="graph-diagram-node-badge"]')?.textContent?.trim()).toBe('RUNNER');

    const edges = el.querySelectorAll('[data-testid="graph-diagram-edge"]');
    expect(edges).toHaveLength(1);
    expect(edges[0].getAttribute('data-edge-kind')).toBe('advance');

    const labels = el.querySelectorAll('[data-testid="graph-diagram-edge-label"]');
    expect(Array.from(labels).map((l) => l.textContent?.trim())).toEqual(['pass', 'fail']);

    const selfLoop = el.querySelector('[data-testid="graph-diagram-self-loop"]');
    expect(selfLoop?.getAttribute('data-node-id')).toBe('n_build');

    expect(el.querySelector('[data-testid="graph-diagram-done"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-diagram-start"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="graph-diagram-start-path"]')).toBeTruthy();
  });

  it('renders a migration sink as a labelled pill, distinct from a node box and the done circle', () => {
    const withMigration: LaidOutGraph = {
      ...LAID_OUT,
      edges: [
        ...LAID_OUT.edges,
        {
          id: 'e2',
          kind: 'advance',
          path: 'M 95 80 L 260 80',
          label: { text: 'basic', x: 180, y: 80, width: 40, height: 20 },
          fromNodeId: 'n_build',
          target: { kind: 'graph', targetGraph: 'bas-dwf' },
          choiceId: 'c_basic',
        },
      ],
      migrations: [{ targetGraph: 'bas-dwf', x: 260, y: 60, width: 100, height: 32 }],
    };
    const fixture = mount({ ok: true, graph: withMigration });
    const el = fixture.nativeElement as HTMLElement;

    const migrations = el.querySelectorAll('[data-testid="graph-diagram-migration"]');
    expect(migrations).toHaveLength(1);
    expect(migrations[0].getAttribute('data-target-graph')).toBe('bas-dwf');
    expect(migrations[0].querySelector('[data-testid="graph-diagram-migration-label"]')?.textContent?.trim()).toBe(
      'bas-dwf',
    );

    const edges = el.querySelectorAll('[data-testid="graph-diagram-edge"]');
    expect(edges).toHaveLength(2);
  });

  it('renders no start indicator when the layout carries none (a degenerate entry_node_id)', () => {
    const fixture = mount({ ok: true, graph: { ...LAID_OUT, start: null } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-diagram-start"]')).toBeNull();
    expect(el.querySelector('[data-testid="graph-diagram-start-path"]')).toBeNull();
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

  it('draws one meta text per wrapped line, stepped down inside the box the layout grew', () => {
    const fixture = mount({ ok: true, graph: LAID_OUT });
    const el = fixture.nativeElement as HTMLElement;
    // Scoped by testid too: the self-loop group carries the same `data-node-id`.
    const buildNode = el.querySelector('[data-testid="graph-diagram-node"][data-node-id="n_build"]') as HTMLElement;

    const metas = buildNode.querySelectorAll('.node-meta');
    expect(Array.from(metas).map((m) => m.textContent?.trim())).toEqual([
      'resume:code · retries 2',
      '→ plan, retrospective',
    ]);
    // Line i sits at `y + META_FIRST_LINE_Y + i * META_LINE_HEIGHT` (20 + 44, then +15),
    // and the last baseline stays inside the box (y 20, height 75 -> bottom 95).
    expect(metas[0].getAttribute('y')).toBe('64');
    expect(metas[1].getAttribute('y')).toBe('79');
  });

  describe('selection', () => {
    it('emits a node selection on click and marks the node data-selected with its incident edges data-incident', () => {
      const fixture = mount({ ok: true, graph: LAID_OUT });
      let received: DiagramSelection | null | undefined;
      fixture.componentInstance.selectionChange.subscribe((v) => (received = v));

      const el = fixture.nativeElement as HTMLElement;
      const buildNode = el.querySelector('[data-testid="graph-diagram-node"][data-node-id="n_build"]') as HTMLElement;
      buildNode.dispatchEvent(new MouseEvent('click', { bubbles: true }));

      expect(received).toEqual({ kind: 'node', nodeId: 'n_build' });

      fixture.componentRef.setInput('selection', received);
      fixture.detectChanges();
      expect(buildNode.getAttribute('data-selected')).toBe('true');
      const edge = el.querySelector('[data-testid="graph-diagram-edge"]') as HTMLElement;
      const selfLoop = el.querySelector('[data-testid="graph-diagram-self-loop"]') as HTMLElement;
      expect(edge.getAttribute('data-incident')).toBe('true');
      expect(selfLoop.getAttribute('data-incident')).toBe('true');
    });

    it("emits an edge selection carrying its endpoints and choiceId on the companion hit path's click", () => {
      const fixture = mount({ ok: true, graph: LAID_OUT });
      const el = fixture.nativeElement as HTMLElement;
      const hitPath = el.querySelector('[data-testid="graph-diagram-edge"] [data-testid="graph-diagram-edge-hit"]') as HTMLElement;

      let received: DiagramSelection | null | undefined;
      fixture.componentInstance.selectionChange.subscribe((v) => (received = v));
      hitPath.dispatchEvent(new MouseEvent('click', { bubbles: true }));

      expect(received).toEqual({
        kind: 'edge',
        edgeId: 'e0',
        fromNodeId: 'n_build',
        target: { kind: 'node', nodeId: 'n_deliver' },
        choiceId: 'c_pass',
        edgeKind: 'advance',
      });
    });

    it('emits the same edge selection when the label pill is clicked instead of the hit path', () => {
      const fixture = mount({ ok: true, graph: LAID_OUT });
      const el = fixture.nativeElement as HTMLElement;
      const labelText = el.querySelector('[data-testid="graph-diagram-edge"] [data-testid="graph-diagram-edge-label"]') as HTMLElement;

      let received: DiagramSelection | null | undefined;
      fixture.componentInstance.selectionChange.subscribe((v) => (received = v));
      labelText.dispatchEvent(new MouseEvent('click', { bubbles: true }));

      expect(received).toEqual({
        kind: 'edge',
        edgeId: 'e0',
        fromNodeId: 'n_build',
        target: { kind: 'node', nodeId: 'n_deliver' },
        choiceId: 'c_pass',
        edgeKind: 'advance',
      });
    });

    it('emits an edge selection carrying the self-loop id when the self-loop group is clicked', () => {
      const fixture = mount({ ok: true, graph: LAID_OUT });
      const el = fixture.nativeElement as HTMLElement;
      const selfLoop = el.querySelector('[data-testid="graph-diagram-self-loop"]') as HTMLElement;

      let received: DiagramSelection | null | undefined;
      fixture.componentInstance.selectionChange.subscribe((v) => (received = v));
      selfLoop.dispatchEvent(new MouseEvent('click', { bubbles: true }));

      expect(received).toEqual({
        kind: 'edge',
        edgeId: 'e1',
        fromNodeId: 'n_build',
        target: { kind: 'node', nodeId: 'n_build' },
        choiceId: 'c_fail',
        edgeKind: 'retry',
      });
    });

    it('emits an edge selection carrying a graph target for a migration edge', () => {
      const withMigration: LaidOutGraph = {
        ...LAID_OUT,
        edges: [
          ...LAID_OUT.edges,
          {
            id: 'e2',
            kind: 'advance',
            path: 'M 95 80 L 260 80',
            label: { text: 'basic', x: 180, y: 80, width: 40, height: 20 },
            fromNodeId: 'n_build',
            target: { kind: 'graph', targetGraph: 'bas-dwf' },
            choiceId: 'c_basic',
          },
        ],
        migrations: [{ targetGraph: 'bas-dwf', x: 260, y: 60, width: 100, height: 32 }],
      };
      const fixture = mount({ ok: true, graph: withMigration });
      const el = fixture.nativeElement as HTMLElement;
      const edges = el.querySelectorAll('[data-testid="graph-diagram-edge"]');
      const hitPath = edges[1].querySelector('[data-testid="graph-diagram-edge-hit"]') as HTMLElement;

      let received: DiagramSelection | null | undefined;
      fixture.componentInstance.selectionChange.subscribe((v) => (received = v));
      hitPath.dispatchEvent(new MouseEvent('click', { bubbles: true }));

      expect(received).toEqual({
        kind: 'edge',
        edgeId: 'e2',
        fromNodeId: 'n_build',
        target: { kind: 'graph', targetGraph: 'bas-dwf' },
        choiceId: 'c_basic',
        edgeKind: 'advance',
      });
    });

    it('emits null when the svg (empty canvas) is clicked', () => {
      const fixture = mount({ ok: true, graph: LAID_OUT }, { kind: 'node', nodeId: 'n_build' });
      const el = fixture.nativeElement as HTMLElement;
      const svg = el.querySelector('[data-testid="graph-diagram-svg"]') as HTMLElement;

      let received: DiagramSelection | null | undefined = 'unset' as unknown as DiagramSelection;
      fixture.componentInstance.selectionChange.subscribe((v) => (received = v));
      svg.dispatchEvent(new MouseEvent('click', { bubbles: true }));

      expect(received).toBeNull();
    });

    it('gives every edge and self-loop exactly one companion hit path with an identical d and a fat transparent stroke', () => {
      const fixture = mount({ ok: true, graph: LAID_OUT });
      const el = fixture.nativeElement as HTMLElement;

      const edgeGroup = el.querySelector('[data-testid="graph-diagram-edge"]') as HTMLElement;
      const edgeHits = edgeGroup.querySelectorAll('[data-testid="graph-diagram-edge-hit"]');
      expect(edgeHits).toHaveLength(1);
      const visiblePath = edgeGroup.querySelector('path.edge') as SVGPathElement;
      expect(edgeHits[0].getAttribute('d')).toBe(visiblePath.getAttribute('d'));
      expect(getComputedStyle(edgeHits[0]).strokeWidth).toBe('14px');

      const selfLoopGroup = el.querySelector('[data-testid="graph-diagram-self-loop"]') as HTMLElement;
      const loopHits = selfLoopGroup.querySelectorAll('[data-testid="graph-diagram-edge-hit"]');
      expect(loopHits).toHaveLength(1);
      const visibleLoopPath = selfLoopGroup.querySelector('path.edge') as SVGPathElement;
      expect(loopHits[0].getAttribute('d')).toBe(visibleLoopPath.getAttribute('d'));
    });

    it('renders a selection passed in from outside highlighted with no click at all (the controlled contract)', () => {
      const fixture = mount(
        { ok: true, graph: LAID_OUT },
        { kind: 'edge', edgeId: 'e0', fromNodeId: 'n_build', target: { kind: 'node', nodeId: 'n_deliver' }, choiceId: 'c_pass', edgeKind: 'advance' },
      );
      const el = fixture.nativeElement as HTMLElement;

      const edge = el.querySelector('[data-testid="graph-diagram-edge"]') as HTMLElement;
      expect(edge.getAttribute('data-selected')).toBe('true');
      const buildNode = el.querySelector('[data-testid="graph-diagram-node"][data-node-id="n_build"]') as HTMLElement;
      const deliverNode = el.querySelector('[data-testid="graph-diagram-node"][data-node-id="n_deliver"]') as HTMLElement;
      expect(buildNode.getAttribute('data-incident')).toBe('true');
      expect(deliverNode.getAttribute('data-incident')).toBe('true');
    });
  });
});
