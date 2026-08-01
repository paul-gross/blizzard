import type { GraphView } from '../api/hub';
import {
  endpointNodeIds,
  incidentEdgeIds,
  resolveSelectedChoice,
  resolveSelectedNode,
  selectionKey,
} from './graph-diagram-selection';
import type { LaidOutGraph } from './graph-layout';

/** A minimal three-node layout: `n_a` has only outgoing edges, `n_b` has both an
 * incoming edge and its own self-loop, `n_c` has only an incoming edge and no
 * edges of its own to speak of. */
const LAID_OUT: LaidOutGraph = {
  width: 400,
  height: 300,
  nodes: [
    { id: 'n_a', name: 'a', executor: 'runner', metaLines: [], x: 0, y: 0, width: 150, height: 60 },
    { id: 'n_b', name: 'b', executor: 'runner', metaLines: [], x: 0, y: 100, width: 150, height: 60 },
    { id: 'n_c', name: 'c', executor: 'hub', metaLines: [], x: 0, y: 200, width: 150, height: 60 },
  ],
  edges: [
    { id: 'e0', kind: 'advance', path: '', label: null, fromNodeId: 'n_a', toNodeId: 'n_b', choiceId: 'c_pass' },
    { id: 'e1', kind: 'advance', path: '', label: null, fromNodeId: 'n_b', toNodeId: 'n_c', choiceId: 'c_pass2' },
    { id: 'e2', kind: 'advance', path: '', label: null, fromNodeId: 'n_c', toNodeId: null, choiceId: 'c_done' },
  ],
  selfLoops: [{ id: 'e3', nodeId: 'n_b', path: '', label: { text: 'fail', x: 0, y: 0, width: 0, height: 0 }, choiceId: 'c_fail' }],
  done: { x: 0, y: 260, r: 24 },
  start: { x: 0, y: -40, r: 24, path: '' },
};

const GRAPH: GraphView = {
  graph_id: 'gr_test',
  name: 'test',
  enabled: true,
  entry_node_id: 'n_a',
  nodes: [
    {
      node_id: 'n_a',
      name: 'a',
      executor: 'runner',
      session: 'fresh',
      judged_by: 'worker',
      choices: [{ choice_id: 'c_pass', name: 'pass', description: 'moves on' }],
    },
    {
      node_id: 'n_b',
      name: 'b',
      executor: 'runner',
      session: 'fresh',
      judged_by: 'worker',
      choices: [
        { choice_id: 'c_pass2', name: 'pass', description: '' },
        { choice_id: 'c_fail', name: 'fail', description: '' },
      ],
    },
    {
      node_id: 'n_c',
      name: 'c',
      executor: 'hub',
      session: 'fresh',
      judged_by: 'none',
      choices: [{ choice_id: 'c_done', name: 'landed', description: '' }],
    },
  ],
  edges: [
    { from_node_id: 'n_a', choice_id: 'c_pass', to_node_name: 'b', prompt_addendum: 'Focus on the happy path.' },
    { from_node_id: 'n_b', choice_id: 'c_pass2', to_node_name: 'c', prompt_addendum: null },
  ],
  warnings: [],
};

describe('selectionKey', () => {
  it('returns null for no selection', () => {
    expect(selectionKey(null)).toBeNull();
  });

  it('keys a node selection and an edge selection distinctly, stable across equal selections', () => {
    expect(selectionKey({ kind: 'node', nodeId: 'n_a' })).toBe(selectionKey({ kind: 'node', nodeId: 'n_a' }));
    const edge: Parameters<typeof selectionKey>[0] = {
      kind: 'edge',
      edgeId: 'e0',
      fromNodeId: 'n_a',
      toNodeId: 'n_b',
      choiceId: 'c_pass',
      edgeKind: 'advance',
    };
    expect(selectionKey(edge)).not.toBe(selectionKey({ kind: 'node', nodeId: 'e0' }));
  });
});

describe('incidentEdgeIds', () => {
  it('finds a node with only outgoing edges', () => {
    expect(incidentEdgeIds(LAID_OUT, 'n_a')).toEqual(['e0']);
  });

  it('finds a node with an incoming edge and an outgoing edge, no self-loop', () => {
    expect(incidentEdgeIds(LAID_OUT, 'n_c')).toEqual(['e1', 'e2']);
  });

  it('finds a node with only an incoming edge', () => {
    const withSink: LaidOutGraph = {
      ...LAID_OUT,
      nodes: [...LAID_OUT.nodes, { id: 'n_d', name: 'd', executor: 'hub', metaLines: [], x: 0, y: 300, width: 150, height: 60 }],
      edges: [...LAID_OUT.edges, { id: 'e4', kind: 'advance', path: '', label: null, fromNodeId: 'n_c', toNodeId: 'n_d', choiceId: 'c_x' }],
    };
    expect(incidentEdgeIds(withSink, 'n_d')).toEqual(['e4']);
  });

  it("includes a node's own self-loop alongside its regular incident edges", () => {
    expect(incidentEdgeIds(LAID_OUT, 'n_b')).toEqual(['e0', 'e1', 'e3']);
  });

  it('returns nothing for a node with no incident edges at all', () => {
    const solo: LaidOutGraph = { ...LAID_OUT, nodes: [LAID_OUT.nodes[0]], edges: [], selfLoops: [] };
    expect(incidentEdgeIds(solo, 'n_a')).toEqual([]);
  });
});

describe('endpointNodeIds', () => {
  it('returns nothing for no selection or a node selection', () => {
    expect(endpointNodeIds(null)).toEqual([]);
    expect(endpointNodeIds({ kind: 'node', nodeId: 'n_a' })).toEqual([]);
  });

  it('resolves both endpoints of a regular edge selection', () => {
    expect(
      endpointNodeIds({ kind: 'edge', edgeId: 'e0', fromNodeId: 'n_a', toNodeId: 'n_b', choiceId: 'c_pass', edgeKind: 'advance' }),
    ).toEqual(['n_a', 'n_b']);
  });

  it('resolves only the source for an edge into the done terminal', () => {
    expect(
      endpointNodeIds({ kind: 'edge', edgeId: 'e2', fromNodeId: 'n_c', toNodeId: null, choiceId: 'c_done', edgeKind: 'advance' }),
    ).toEqual(['n_c']);
  });

  it('resolves a single node for a self-loop selection', () => {
    expect(
      endpointNodeIds({ kind: 'edge', edgeId: 'e3', fromNodeId: 'n_b', toNodeId: 'n_b', choiceId: 'c_fail', edgeKind: 'retry' }),
    ).toEqual(['n_b']);
  });
});

describe('resolveSelectedNode', () => {
  it('resolves the selected node from the wire graph', () => {
    expect(resolveSelectedNode(GRAPH, { kind: 'node', nodeId: 'n_b' })?.name).toBe('b');
  });

  it('returns null for no selection or an edge selection', () => {
    expect(resolveSelectedNode(GRAPH, null)).toBeNull();
    expect(
      resolveSelectedNode(GRAPH, { kind: 'edge', edgeId: 'e0', fromNodeId: 'n_a', toNodeId: 'n_b', choiceId: 'c_pass', edgeKind: 'advance' }),
    ).toBeNull();
  });

  it('returns null for a node id that matches nothing in the graph', () => {
    expect(resolveSelectedNode(GRAPH, { kind: 'node', nodeId: 'n_ghost' })).toBeNull();
  });
});

describe('resolveSelectedChoice', () => {
  it("resolves an edge selection's choice name, description, endpoints, and prompt addendum", () => {
    const resolved = resolveSelectedChoice(GRAPH, {
      kind: 'edge',
      edgeId: 'e0',
      fromNodeId: 'n_a',
      toNodeId: 'n_b',
      choiceId: 'c_pass',
      edgeKind: 'advance',
    });
    expect(resolved).toEqual({
      choiceId: 'c_pass',
      name: 'pass',
      description: 'moves on',
      fromNodeId: 'n_a',
      toNodeId: 'n_b',
      edgeKind: 'advance',
      promptAddendum: 'Focus on the happy path.',
    });
  });

  it('resolves an edge into done with toNodeId null', () => {
    const resolved = resolveSelectedChoice(GRAPH, {
      kind: 'edge',
      edgeId: 'e2',
      fromNodeId: 'n_c',
      toNodeId: null,
      choiceId: 'c_done',
      edgeKind: 'advance',
    });
    expect(resolved?.toNodeId).toBeNull();
    expect(resolved?.name).toBe('landed');
  });

  it('resolves null promptAddendum for an edge with none and for one matching no entry in graph.edges', () => {
    expect(
      resolveSelectedChoice(GRAPH, {
        kind: 'edge',
        edgeId: 'e1',
        fromNodeId: 'n_b',
        toNodeId: 'n_c',
        choiceId: 'c_pass2',
        edgeKind: 'advance',
      })?.promptAddendum,
    ).toBeNull();
    expect(
      resolveSelectedChoice(GRAPH, {
        kind: 'edge',
        edgeId: 'e2',
        fromNodeId: 'n_c',
        toNodeId: null,
        choiceId: 'c_done',
        edgeKind: 'advance',
      })?.promptAddendum,
    ).toBeNull();
  });

  it('falls back to the raw choiceId when it matches no choice on the source node, mirroring graph-detail.ts', () => {
    const resolved = resolveSelectedChoice(GRAPH, {
      kind: 'edge',
      edgeId: 'e9',
      fromNodeId: 'n_a',
      toNodeId: 'n_b',
      choiceId: 'c_unknown',
      edgeKind: 'advance',
    });
    expect(resolved?.name).toBe('c_unknown');
    expect(resolved?.description).toBe('');
  });

  it('returns null for no selection or a node selection', () => {
    expect(resolveSelectedChoice(GRAPH, null)).toBeNull();
    expect(resolveSelectedChoice(GRAPH, { kind: 'node', nodeId: 'n_a' })).toBeNull();
  });
});
