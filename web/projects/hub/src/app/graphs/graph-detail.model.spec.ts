import type { GraphNodeView, GraphView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { entryNodeName, graphOverrideRetired } from './graph-detail.model';

const NODES: readonly GraphNodeView[] = [
  { node_id: 'nd_1', name: 'survey', executor: 'runner', session: 'fresh', judged_by: 'worker' },
  { node_id: 'nd_2', name: 'report', executor: 'runner', session: 'fresh', judged_by: 'worker' },
];

const GRAPH: GraphView = {
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
