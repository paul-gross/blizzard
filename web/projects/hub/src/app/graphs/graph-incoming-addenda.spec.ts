import type { Graph, GraphNode } from './graph.domain';
import { incomingAddenda } from './graph-incoming-addenda';

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

const GRAPH: Graph = {
  ...GRAPH_DEFAULTS,
  graphId: 'gr_test',
  name: 'test',
  entryNodeId: 'n_plan',
  nodes: [
    {
      ...NODE_DEFAULTS,
      nodeId: 'n_plan',
      name: 'plan',
      executor: 'runner',
      session: 'fresh',
      judgedBy: 'worker',
      choices: [{ choiceId: 'c_advance', name: 'advance', description: '' }],
    },
    {
      ...NODE_DEFAULTS,
      nodeId: 'n_review',
      name: 'review',
      executor: 'runner',
      session: 'fresh',
      judgedBy: 'worker',
      choices: [
        { choiceId: 'c_pass', name: 'pass', description: '' },
        { choiceId: 'c_failed', name: 'review-failed', description: '' },
      ],
    },
    {
      ...NODE_DEFAULTS,
      nodeId: 'n_build',
      name: 'build',
      executor: 'runner',
      session: 'resume',
      judgedBy: 'worker',
      choices: [],
    },
  ],
  edges: [
    { ...EDGE_DEFAULTS, fromNodeId: 'n_plan', choiceId: 'c_advance', toNodeName: 'build', promptAddendum: 'Arriving fresh from plan.' },
    { ...EDGE_DEFAULTS, fromNodeId: 'n_review', choiceId: 'c_failed', toNodeName: 'build', promptAddendum: 'Fix the review findings.' },
    // No addendum: excluded even though it targets `build`.
    { ...EDGE_DEFAULTS, fromNodeId: 'n_review', choiceId: 'c_pass', toNodeName: 'deliver', promptAddendum: 'Ship it.' },
    // Empty-string addendum: also excluded.
    { ...EDGE_DEFAULTS, fromNodeId: 'n_plan', choiceId: 'c_advance', toNodeName: 'review', promptAddendum: '' },
  ],
};

describe('incomingAddenda', () => {
  it('collects every inbound edge with a non-empty promptAddendum, labelled by source node and choice', () => {
    const build = GRAPH.nodes!.find((n) => n.nodeId === 'n_build')!;
    expect(incomingAddenda(GRAPH, build)).toEqual([
      { fromNodeName: 'plan', choiceName: 'advance', promptAddendum: 'Arriving fresh from plan.' },
      { fromNodeName: 'review', choiceName: 'review-failed', promptAddendum: 'Fix the review findings.' },
    ]);
  });

  it('returns an empty list for a node with no inbound edges at all', () => {
    const plan = GRAPH.nodes!.find((n) => n.nodeId === 'n_plan')!;
    expect(incomingAddenda(GRAPH, plan)).toEqual([]);
  });

  it('excludes an inbound edge whose promptAddendum is null, undefined, or empty', () => {
    const review = GRAPH.nodes!.find((n) => n.nodeId === 'n_review')!;
    expect(incomingAddenda(GRAPH, review)).toEqual([]);
  });

  it('falls back to the raw fromNodeId and choiceId when they match nothing in the graph', () => {
    const graph: Graph = {
      ...GRAPH,
      edges: [{ ...EDGE_DEFAULTS, fromNodeId: 'n_missing', choiceId: 'c_missing', toNodeName: 'build', promptAddendum: 'Orphaned.' }],
    };
    const build = graph.nodes!.find((n) => n.nodeId === 'n_build')!;
    expect(incomingAddenda(graph, build)).toEqual([
      { fromNodeName: 'n_missing', choiceName: 'c_missing', promptAddendum: 'Orphaned.' },
    ]);
  });

  it('handles a graph with no edges at all', () => {
    const graph: Graph = { ...GRAPH, edges: [] };
    const build = graph.nodes!.find((n) => n.nodeId === 'n_build')!;
    expect(incomingAddenda(graph, build)).toEqual([]);
  });
});
