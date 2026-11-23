// The graph domain builders specs build their inputs from — domain literals only, no wire, no Angular.
// The wire builders (`graphView`, `graphSummaryView` in `fleet/testing`) stay for the translator, query, and
// mutation specs.
import { fixtureInstant } from 'fleet/testing';

import type { Graph, GraphEdge, GraphNode, GraphSession, GraphSummary } from './graph.domain';

/** The effective version of graph `build`, minted a day ago. */
export function graphSummary(overrides: Partial<GraphSummary> = {}): GraphSummary {
  return {
    graphId: 'gr_build',
    name: 'build',
    createdAt: fixtureInstant(-24 * 3600),
    effective: true,
    retired: false,
    lifecycle: 'effective',
    ...overrides,
  };
}

/** A worker node with no prompt, checks, or choices, at whatever id and name the caller names. */
export function graphNode(overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    nodeId: 'nd_build',
    name: 'build',
    executor: 'runner',
    session: 'fresh',
    sessionSource: null,
    judgedBy: 'worker',
    judgementPrompt: null,
    action: null,
    actionArgs: {},
    prompt: null,
    checks: [],
    produces: [],
    retriesMax: null,
    retriesExhausted: null,
    choices: [],
    ...overrides,
  };
}

/** A node-to-node edge, `build` on to `review`. */
export function graphEdge(overrides: Partial<GraphEdge> = {}): GraphEdge {
  return {
    choiceId: 'ch_pass',
    fromNodeId: 'nd_build',
    toNodeName: 'review',
    targetKind: 'node',
    targetGraph: null,
    promptAddendum: null,
    ...overrides,
  };
}

/** A session declaration `code` with no preferences and no rotation bounds. */
export function graphSession(overrides: Partial<GraphSession> = {}): GraphSession {
  return { name: 'code', model: [], harnesses: [], effort: null, compactionWindow: null, rotate: null, ...overrides };
}

/** An enabled two-node graph, `build` then `review`, entered at `build`. */
export function graph(overrides: Partial<Graph> = {}): Graph {
  return {
    graphId: 'gr_build',
    name: 'build',
    entryNodeId: 'nd_build',
    retired: false,
    nodes: [graphNode(), graphNode({ nodeId: 'nd_review', name: 'review' })],
    edges: [graphEdge()],
    sessions: [],
    ...overrides,
  };
}
