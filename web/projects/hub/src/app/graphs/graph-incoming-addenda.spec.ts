import type { GraphView, GraphNodeView } from 'fleet';
import { incomingAddenda } from './graph-incoming-addenda';

const GRAPH_DEFAULTS: Pick<GraphView, 'artifacts' | 'edges' | 'follow_latest' | 'retired' | 'sessions' | 'warnings'> = {
  artifacts: [],
  edges: [],
  follow_latest: null,
  retired: false,
  sessions: [],
  warnings: [],
};

const NODE_DEFAULTS: Pick<GraphNodeView, 'action' | 'action_args' | 'checks' | 'checks_cwd' | 'checks_timeout' | 'choices' | 'judgement_prompt' | 'produces' | 'prompt' | 'retries_exhausted' | 'retries_max' | 'session_source'> = {
  action: null,
  action_args: {},
  checks: [],
  checks_cwd: null,
  checks_timeout: null,
  choices: [],
  judgement_prompt: null,
  produces: [],
  prompt: null,
  retries_exhausted: null,
  retries_max: null,
  session_source: null,
};

const EDGE_DEFAULTS: Pick<GraphView['edges'][number], 'prompt_addendum' | 'target_graph' | 'target_kind'> = {
  prompt_addendum: null,
  target_graph: null,
  target_kind: 'node',
};

const GRAPH: GraphView = {
  ...GRAPH_DEFAULTS,
  graph_id: 'gr_test',
  name: 'test',
  enabled: true,
  entry_node_id: 'n_plan',
  nodes: [
    {
      ...NODE_DEFAULTS,
      node_id: 'n_plan',
      name: 'plan',
      executor: 'runner',
      session: 'fresh',
      judged_by: 'worker',
      choices: [{ requires_checks: false, choice_id: 'c_advance', name: 'advance', description: '' }],
    },
    {
      ...NODE_DEFAULTS,
      node_id: 'n_review',
      name: 'review',
      executor: 'runner',
      session: 'fresh',
      judged_by: 'worker',
      choices: [
        { requires_checks: false, choice_id: 'c_pass', name: 'pass', description: '' },
        { requires_checks: false, choice_id: 'c_failed', name: 'review-failed', description: '' },
      ],
    },
    {
      ...NODE_DEFAULTS,
      node_id: 'n_build',
      name: 'build',
      executor: 'runner',
      session: 'resume',
      judged_by: 'worker',
      choices: [],
    },
  ],
  edges: [
    { ...EDGE_DEFAULTS, from_node_id: 'n_plan', choice_id: 'c_advance', to_node_name: 'build', prompt_addendum: 'Arriving fresh from plan.' },
    { ...EDGE_DEFAULTS, from_node_id: 'n_review', choice_id: 'c_failed', to_node_name: 'build', prompt_addendum: 'Fix the review findings.' },
    // No addendum: excluded even though it targets `build`.
    { ...EDGE_DEFAULTS, from_node_id: 'n_review', choice_id: 'c_pass', to_node_name: 'deliver', prompt_addendum: 'Ship it.' },
    // Empty-string addendum: also excluded.
    { ...EDGE_DEFAULTS, from_node_id: 'n_plan', choice_id: 'c_advance', to_node_name: 'review', prompt_addendum: '' },
  ],
  warnings: [],
};

describe('incomingAddenda', () => {
  it('collects every inbound edge with a non-empty prompt_addendum, labelled by source node and choice', () => {
    const build = GRAPH.nodes!.find((n) => n.node_id === 'n_build')!;
    expect(incomingAddenda(GRAPH, build)).toEqual([
      { fromNodeName: 'plan', choiceName: 'advance', promptAddendum: 'Arriving fresh from plan.' },
      { fromNodeName: 'review', choiceName: 'review-failed', promptAddendum: 'Fix the review findings.' },
    ]);
  });

  it('returns an empty list for a node with no inbound edges at all', () => {
    const plan = GRAPH.nodes!.find((n) => n.node_id === 'n_plan')!;
    expect(incomingAddenda(GRAPH, plan)).toEqual([]);
  });

  it('excludes an inbound edge whose prompt_addendum is null, undefined, or empty', () => {
    const review = GRAPH.nodes!.find((n) => n.node_id === 'n_review')!;
    expect(incomingAddenda(GRAPH, review)).toEqual([]);
  });

  it('falls back to the raw from_node_id and choice_id when they match nothing in the graph', () => {
    const graph: GraphView = {
      ...GRAPH,
      edges: [{ ...EDGE_DEFAULTS, from_node_id: 'n_missing', choice_id: 'c_missing', to_node_name: 'build', prompt_addendum: 'Orphaned.' }],
    };
    const build = graph.nodes!.find((n) => n.node_id === 'n_build')!;
    expect(incomingAddenda(graph, build)).toEqual([
      { fromNodeName: 'n_missing', choiceName: 'c_missing', promptAddendum: 'Orphaned.' },
    ]);
  });

  it('handles a graph with no edges at all', () => {
    const graph: GraphView = { ...GRAPH, edges: [] };
    const build = graph.nodes!.find((n) => n.node_id === 'n_build')!;
    expect(incomingAddenda(graph, build)).toEqual([]);
  });
});
