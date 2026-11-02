import { type GraphView } from 'fleet';
import { graphEdgeView, graphNodeView, graphView, transitionView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import {
  graphStepLabel,
  groupBlocks,
  isDenseStrip,
  type NodeBlock,
  type NodeBlockState,
  nodeBlocks,
  orderGraphNodes,
  runnerBoardLabel,
  showsNodeStrip,
  stripGroups,
} from './node-strip.model';

/** A graph over `names`, entered at the first, with an edge per `from>to` pair; a node's id is `nd_<prefix><name>`. */
function graph(names: readonly string[], edges: readonly string[], prefix = ''): GraphView {
  const id = (name: string) => `nd_${prefix}${name}`;
  return graphView({
    entry_node_id: id(names[0]),
    nodes: names.map((name) => graphNodeView({ node_id: id(name), name })),
    edges: edges.map((pair, i) => {
      const [from, to] = pair.split('>');
      return to === 'done'
        ? graphEdgeView({ choice_id: `c${i}`, from_node_id: id(from), target_kind: 'done', to_node_name: '' })
        : graphEdgeView({ choice_id: `c${i}`, from_node_id: id(from), to_node_name: to });
    }),
  });
}

/** One completed visit: left `from` for `to` at `epoch`. */
const step = (epoch: number, from: string, to: string | null) => transitionView({ epoch, from_node_name: from, to_node_name: to });

/** The development loop: build, review (which can send it back to build), land, and an unreachable orphan. */
const DEV = graph(['build', 'review', 'land', 'orphan'], ['build>review', 'review>build', 'review>land', 'land>done']);

/** Each block's state by its node's name. */
const states = (blocks: readonly NodeBlock[]): Partial<Record<'build' | 'review' | 'land' | 'orphan', NodeBlockState>> =>
  Object.fromEntries(blocks.map((b) => [b.name, b.state]));

describe('orderGraphNodes', () => {
  it('orders start to finish whatever order the graph lists its nodes in', () => {
    const shuffled: GraphView = { ...DEV, nodes: [...DEV.nodes].reverse() };
    expect(orderGraphNodes(shuffled)).toEqual(['nd_build', 'nd_review', 'nd_land', 'nd_orphan']);
  });

  it('never lets a rework loop push its target later', () => {
    const looped = graph(['build', 'test', 'review'], ['build>test', 'test>review', 'test>build', 'review>build', 'review>test']);
    expect(orderGraphNodes(looped)).toEqual(['nd_build', 'nd_test', 'nd_review']);
  });

  it('ranks a node by its longest forward path, so a shortcut never pulls it earlier', () => {
    const shortcut = graph(['build', 'review', 'test'], ['build>review', 'build>test', 'test>review']);
    expect(orderGraphNodes(shortcut)).toEqual(['nd_build', 'nd_test', 'nd_review']);
  });

  it('keeps discovery order between nodes at the same depth', () => {
    const fork = graph(['plan', 'web', 'api', 'join'], ['plan>web', 'plan>api', 'web>join', 'api>join']);
    expect(orderGraphNodes(fork)).toEqual(['nd_plan', 'nd_web', 'nd_api', 'nd_join']);
  });

  it('follows the reached nodes with the unreached, in the graph\'s own order', () => {
    expect(orderGraphNodes(DEV).slice(-1)).toEqual(['nd_orphan']);
    expect(orderGraphNodes({ ...DEV, entry_node_id: 'nd_missing' })).toEqual(['nd_build', 'nd_review', 'nd_land', 'nd_orphan']);
  });
});

describe('nodeBlocks', () => {
  it('draws one block per node, in order, every one unvisited before any history', () => {
    const blocks = nodeBlocks(DEV, [], [], null);
    expect(blocks.map((b) => b.nodeId)).toEqual(['nd_build', 'nd_review', 'nd_land', 'nd_orphan']);
    expect(blocks.every((b) => b.state === 'unvisited')).toBe(true);
  });

  it('passes a node left forward and fails one routed back to itself or earlier', () => {
    const history = [step(1, 'build', 'build'), step(2, 'review', 'build')];
    expect(states(nodeBlocks(DEV, history, [], null))).toMatchObject({ build: 'failed', review: 'failed' });
    expect(states(nodeBlocks(DEV, [step(1, 'build', 'review')], [], null))).toMatchObject({ build: 'passed', review: 'unvisited' });
  });

  it('reads a node by its last visit only', () => {
    const reworked = [step(1, 'build', 'review'), step(2, 'review', 'build'), step(3, 'build', 'review'), step(4, 'review', 'land')];
    expect(states(nodeBlocks(DEV, reworked, [], 'nd_land'))).toEqual({ build: 'passed', review: 'passed', land: 'current', orphan: 'unvisited' });

    const bounced = [step(1, 'build', 'review'), step(2, 'review', 'land'), step(3, 'land', 'review')];
    expect(states(nodeBlocks(DEV, bounced, [], 'nd_review'))).toMatchObject({ land: 'failed', review: 'current' });
  });

  it('takes visits in epoch order, not the order they are handed in', () => {
    const outOfOrder = [step(4, 'review', 'land'), step(2, 'review', 'build')];
    expect(states(nodeBlocks(DEV, outOfOrder, [], null)).review).toBe('passed');
  });

  it('passes a node left for the end of the graph', () => {
    expect(states(nodeBlocks(DEV, [step(1, 'land', null)], [], null)).land).toBe('passed');
  });

  it('shows the current node as current whatever its history says', () => {
    expect(states(nodeBlocks(DEV, [step(1, 'build', 'build')], [], 'nd_build')).build).toBe('current');
  });

  it('matches visits by name, so history under an older version\'s ids still lights the newer graph', () => {
    const v2 = graph(['build', 'review', 'land', 'orphan'], ['build>review', 'review>build', 'review>land'], 'v2_');
    const v1History = [transitionView({ epoch: 1, from_node_id: 'nd_v1_build', from_node_name: 'build', to_node_id: 'nd_v1_review', to_node_name: 'review' })];
    expect(states(nodeBlocks(v2, v1History, [], 'nd_v2_review'))).toMatchObject({ build: 'passed', review: 'current' });
  });

  it('reads a migration as a visit, interleaved with the transitions by epoch', () => {
    const history = [step(1, 'build', 'review'), step(4, 'build', 'review')];
    const migrations = [{ epoch: 2, from_node_name: 'review', landed_node_name: 'build' }];
    expect(states(nodeBlocks(DEV, history, migrations, null))).toMatchObject({ build: 'passed', review: 'failed' });
    expect(states(nodeBlocks(DEV, [], [{ epoch: null, from_node_name: 'build', landed_node_name: 'review' }], null)).build).toBe('passed');
  });

  it('skips a visit from a node the graph no longer names', () => {
    const blocks = nodeBlocks(DEV, [step(1, 'retired-step', 'build'), step(2, 'build', 'retired-step')], [{ epoch: 3, from_node_name: null }], null);
    expect(blocks).toHaveLength(4);
    expect(states(blocks)).toMatchObject({ build: 'passed', review: 'unvisited' });
  });
});

describe('groupBlocks', () => {
  const blocks = (n: number): NodeBlock[] => Array.from({ length: n }, (_, i) => ({ nodeId: `nd_${i}`, name: `n${i}`, state: 'unvisited' }));

  it('breaks the strip into groups of ten, the last holding the rest', () => {
    expect(groupBlocks(blocks(25)).map((g) => g.length)).toEqual([10, 10, 5]);
    expect(groupBlocks(blocks(10)).map((g) => g.length)).toEqual([10]);
    expect(groupBlocks(blocks(11)).map((g) => g.length)).toEqual([10, 1]);
    expect(groupBlocks([])).toEqual([]);
  });

  it('packs the strip dense past twenty blocks', () => {
    expect(isDenseStrip(groupBlocks(blocks(20)))).toBe(false);
    expect(isDenseStrip(groupBlocks(blocks(21)))).toBe(true);
  });
});

describe('stripGroups', () => {
  it('draws nothing until both the graph and the detail are read', () => {
    expect(stripGroups(undefined, { history: [] }, null)).toEqual([]);
    expect(stripGroups(DEV, undefined, null)).toEqual([]);
  });

  it('reads a detail with no history or migrations as all unvisited', () => {
    const [group] = stripGroups(DEV, {}, 'nd_build');
    expect(states(group)).toEqual({ build: 'current', review: 'unvisited', land: 'unvisited', orphan: 'unvisited' });
  });
});

describe('showsNodeStrip', () => {
  it('shows only in the mid-graph lanes — running, waiting on a human, needing one', () => {
    expect(showsNodeStrip({ status: 'running', terminal: false })).toBe(true);
    expect(showsNodeStrip({ status: 'delivering', terminal: false })).toBe(true);
    expect(showsNodeStrip({ status: 'waiting_on_human', terminal: false })).toBe(true);
    expect(showsNodeStrip({ status: 'needs_human', terminal: false })).toBe(true);
    expect(showsNodeStrip({ status: 'ready', terminal: false })).toBe(false);
    expect(showsNodeStrip({ status: 'not_ready', terminal: false })).toBe(false);
    expect(showsNodeStrip({ status: 'done', terminal: true })).toBe(false);
  });

  it('hides on a terminal chunk whatever lane its status reads as', () => {
    expect(showsNodeStrip({ status: 'stopped', terminal: true })).toBe(false);
    expect(showsNodeStrip({ status: 'running', terminal: true })).toBe(false);
  });
});

describe('graphStepLabel', () => {
  it('prefixes a node label with its graph once the name is known', () => {
    expect(graphStepLabel('adv-dwf', 'build')).toBe('adv-dwf/build');
    expect(graphStepLabel(null, 'build')).toBe('build');
    expect(graphStepLabel(undefined, 'build')).toBe('build');
    expect(graphStepLabel('', 'build')).toBe('build');
  });

  it('leaves a status word standing in for the node bare', () => {
    expect(graphStepLabel('adv-dwf', 'build', 'stopped')).toBe('stopped');
    expect(graphStepLabel('adv-dwf', 'build', 'build')).toBe('adv-dwf/build');
  });
});

describe('runnerBoardLabel', () => {
  it('writes host/name, or the bare name with no host', () => {
    expect(runnerBoardLabel({ host: 'xps', name: 'r-claude' })).toBe('xps/r-claude');
    expect(runnerBoardLabel({ host: null, name: 'r-claude' })).toBe('r-claude');
  });
});
