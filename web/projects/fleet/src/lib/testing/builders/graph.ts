import type { GraphEdgeView, GraphNodeView, GraphView } from '../../api/hub';

/** A worker node with no prompt, checks, or choices, at whatever id and name the caller names. */
export function graphNodeView(overrides: Partial<GraphNodeView> = {}): GraphNodeView {
  return {
    node_id: 'nd_build',
    name: 'build',
    executor: 'runner',
    session: 'fresh',
    judged_by: 'worker',
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
    ...overrides,
  };
}

/** A node-to-node edge, `build` on to `review`. */
export function graphEdgeView(overrides: Partial<GraphEdgeView> = {}): GraphEdgeView {
  return {
    choice_id: 'ch_pass',
    from_node_id: 'nd_build',
    to_node_name: 'review',
    target_kind: 'node',
    target_graph: null,
    prompt_addendum: null,
    ...overrides,
  };
}

/** An enabled two-node graph, `build` then `review`, entered at `build`. */
export function graphView(overrides: Partial<GraphView> = {}): GraphView {
  return {
    graph_id: 'gr_build',
    name: 'build',
    entry_node_id: 'nd_build',
    nodes: [graphNodeView(), graphNodeView({ node_id: 'nd_review', name: 'review' })],
    edges: [graphEdgeView()],
    enabled: true,
    retired: false,
    follow_latest: null,
    artifacts: [],
    definition_yaml: '',
    sessions: [],
    warnings: [],
    ...overrides,
  };
}
