import type { GraphNodeView, GraphView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { entryNodeName, graphOverrideRetired } from './graph-detail.model';

const NODE_DEFAULTS: Pick<GraphNodeView, 'checks' | 'checks_cwd' | 'checks_timeout' | 'choices' | 'judgement_prompt' | 'produces' | 'prompt' | 'retries_exhausted' | 'retries_max' | 'session_source'> = {
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

const GRAPH_DEFAULTS: Pick<GraphView, 'artifacts' | 'edges' | 'follow_latest' | 'retired' | 'sessions' | 'warnings'> = {
  artifacts: [],
  edges: [],
  follow_latest: null,
  retired: false,
  sessions: [],
  warnings: [],
};

const NODES: readonly GraphNodeView[] = [
  { ...NODE_DEFAULTS, node_id: 'nd_1', name: 'survey', executor: 'runner', session: 'fresh', judged_by: 'worker' },
  { ...NODE_DEFAULTS, node_id: 'nd_2', name: 'report', executor: 'runner', session: 'fresh', judged_by: 'worker' },
];

const GRAPH: GraphView = {
  ...GRAPH_DEFAULTS,
  graph_id: 'gr_1',
  name: 'garden-routine',
  entry_node_id: 'nd_2',
  enabled: true,
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
    expect(entryNodeName({ ...GRAPH, entry_node_id: 'nd_9' }, NODES)).toBe('nd_9');
  });
});
