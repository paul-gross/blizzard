import type { GraphNode, Graph } from './graph.domain';
import { describe, expect, it } from 'vitest';

import { entryNodeName, graphOverrideRetired } from './graph-detail.model';

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

const GRAPH_DEFAULTS: Pick<Graph, 'edges' | 'retired' | 'sessions'> = {
  edges: [],
  retired: false,
  sessions: [],
};

const NODES: readonly GraphNode[] = [
  { ...NODE_DEFAULTS, nodeId: 'nd_1', name: 'survey', executor: 'runner', session: 'fresh', judgedBy: 'worker' },
  { ...NODE_DEFAULTS, nodeId: 'nd_2', name: 'report', executor: 'runner', session: 'fresh', judgedBy: 'worker' },
];

const GRAPH: Graph = {
  ...GRAPH_DEFAULTS,
  graphId: 'gr_1',
  name: 'garden-routine',
  entryNodeId: 'nd_2',
  nodes: [...NODES],
};

describe('graphOverrideRetired', () => {
  it('answers null while nothing is pending for the graph', () => {
    expect(graphOverrideRetired('gr_1', [{ graphId: 'gr_2', retired: true }])).toBeNull();
    expect(graphOverrideRetired('gr_1', [])).toBeNull();
  });

  it("answers the pending mutation's retired flag", () => {
    expect(graphOverrideRetired('gr_1', [{ graphId: 'gr_1', retired: true }])).toBe(true);
    expect(graphOverrideRetired('gr_1', [{ graphId: 'gr_1', retired: false }])).toBe(false);
  });
});

describe('entryNodeName', () => {
  it('answers an empty name before the graph resolves', () => {
    expect(entryNodeName(undefined, NODES)).toBe('');
  });

  it("answers the entry node's name", () => {
    expect(entryNodeName(GRAPH, NODES)).toBe('report');
  });

  it('falls back to the raw entry node id when no node carries it', () => {
    expect(entryNodeName({ ...GRAPH, entryNodeId: 'nd_9' }, NODES)).toBe('nd_9');
  });
});
