import type { GraphView } from '../api/hub';
import { type TextMeasurer, layoutGraph } from './graph-layout';

/** Deterministic stand-in for canvas `measureText` — a fixed per-character width so
 * layout math is reproducible without a DOM (the production measurer lives in
 * `graph-diagram.ts` and is exercised there). */
const measure: TextMeasurer = (text) => text.length * 7;

/** Mirrors `default.yaml`'s shape (`bzh:generated-client` fixture: build -> review ->
 * deliver): a self-loop (`build` fail-back into itself), two back edges (`review`'s
 * fail into `build`, `deliver`'s conflict into `build`), and a `done` terminal. */
const DEFAULT_LIKE: GraphView = {
  graph_id: 'gr_default_v1',
  name: 'default-delivery',
  enabled: true,
  entry_node_id: 'n_build',
  nodes: [
    {
      node_id: 'n_build',
      name: 'build',
      executor: 'runner',
      session: 'resume',
      judged_by: 'worker',
      retries_max: 2,
      choices: [
        { choice_id: 'c_pass', name: 'pass', description: '' },
        { choice_id: 'c_fail', name: 'fail', description: '' },
      ],
    },
    {
      node_id: 'n_review',
      name: 'review',
      executor: 'runner',
      session: 'fresh',
      judged_by: 'worker',
      retries_max: 2,
      produces: ['review-findings'],
      choices: [
        { choice_id: 'c_pass2', name: 'pass', description: '' },
        { choice_id: 'c_fail2', name: 'fail', description: '' },
      ],
    },
    {
      node_id: 'n_deliver',
      name: 'deliver',
      executor: 'hub',
      session: 'fresh',
      judged_by: 'none',
      mode: 'merge-to-main',
      choices: [
        { choice_id: 'c_landed', name: 'landed', description: '' },
        { choice_id: 'c_conflict', name: 'conflict', description: '' },
      ],
    },
  ],
  edges: [
    { from_node_id: 'n_build', choice_id: 'c_pass', to_node_name: 'review' },
    { from_node_id: 'n_build', choice_id: 'c_fail', to_node_name: 'build' },
    { from_node_id: 'n_review', choice_id: 'c_pass2', to_node_name: 'deliver' },
    { from_node_id: 'n_review', choice_id: 'c_fail2', to_node_name: 'build' },
    { from_node_id: 'n_deliver', choice_id: 'c_landed', to_node_name: 'done' },
    { from_node_id: 'n_deliver', choice_id: 'c_conflict', to_node_name: 'build' },
  ],
  warnings: [],
};

describe('layoutGraph', () => {
  it('lays out the default-delivery shape: 3 node boxes, done sink, one self-loop extracted', () => {
    const outcome = layoutGraph(DEFAULT_LIKE, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;

    expect(outcome.graph.nodes).toHaveLength(3);
    expect(outcome.graph.nodes.map((n) => n.id).sort()).toEqual(['n_build', 'n_deliver', 'n_review']);
    expect(outcome.graph.done).not.toBeNull();

    // The self-loop (build -fail-> build) is filtered out of the dagre edge set and
    // surfaces only in `selfLoops`, never in `edges`.
    expect(outcome.graph.selfLoops).toHaveLength(1);
    expect(outcome.graph.selfLoops[0].nodeId).toBe('n_build');
    expect(outcome.graph.edges).toHaveLength(5);
    // Edges resolve their label to the choice's NAME, not the raw wire `choice_id`.
    expect(outcome.graph.edges.every((e) => e.label?.text !== 'c_fail')).toBe(true);
    expect(outcome.graph.edges.every((e) => !e.label?.text.startsWith('c_'))).toBe(true);
  });

  it('labels edges with the choice NAME resolved from the source node, not the raw choice_id', () => {
    const outcome = layoutGraph(DEFAULT_LIKE, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;

    // `e0`..`e5` are `resolved`'s indices, in the same order as `DEFAULT_LIKE.edges`.
    const byId = (id: string) => outcome.graph.edges.find((e) => e.id === id);

    expect(byId('e0')?.label?.text).toBe('pass'); // build -c_pass-> review
    expect(byId('e2')?.label?.text).toBe('pass'); // review -c_pass2-> deliver
    expect(byId('e4')?.label?.text).toBe('landed'); // deliver -c_landed-> done
    expect(byId('e3')?.label?.text).toBe('fail'); // review -c_fail2-> build (back edge)
    expect(byId('e5')?.label?.text).toBe('conflict'); // deliver -c_conflict-> build (back edge)
    expect(outcome.graph.selfLoops[0].label.text).toBe('fail'); // build -c_fail-> build
  });

  it('assigns semantic edge kinds structurally: forward edges advance, back/self-loop edges retry', () => {
    const outcome = layoutGraph(DEFAULT_LIKE, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;

    const byId = (id: string) => outcome.graph.edges.find((e) => e.id === id);

    expect(byId('e0')?.kind).toBe('advance'); // build -> review
    expect(byId('e2')?.kind).toBe('advance'); // review -> deliver
    expect(byId('e4')?.kind).toBe('advance'); // deliver -> done
    expect(byId('e3')?.kind).toBe('retry'); // review -> build (back edge)
    expect(byId('e5')?.kind).toBe('retry'); // deliver -> build (back edge)
  });

  it('reserves edge-label space sized to the resolved choice name, not a fixed width', () => {
    const outcome = layoutGraph(DEFAULT_LIKE, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;

    const short = outcome.graph.edges.find((e) => e.id === 'e0'); // "pass"
    const long = outcome.graph.edges.find((e) => e.id === 'e5'); // "conflict"
    expect(short?.label?.width).toBeGreaterThan(0);
    expect(long?.label?.width).toBeGreaterThan(short?.label?.width ?? 0);
  });

  it('lays out a degenerate single-node graph with no edges, no self-loops, no done sink', () => {
    const single: GraphView = {
      graph_id: 'gr_solo',
      name: 'solo',
      enabled: true,
      entry_node_id: 'n_only',
      nodes: [{ node_id: 'n_only', name: 'only', executor: 'runner', session: 'fresh', judged_by: 'worker', choices: [] }],
      edges: [],
      warnings: [],
    };
    const outcome = layoutGraph(single, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.graph.nodes).toHaveLength(1);
    expect(outcome.graph.edges).toHaveLength(0);
    expect(outcome.graph.selfLoops).toHaveLength(0);
    expect(outcome.graph.done).toBeNull();
  });

  it('falls back to { ok: false } for an empty graph (no nodes)', () => {
    const empty: GraphView = {
      graph_id: 'gr_empty',
      name: 'empty',
      enabled: true,
      entry_node_id: 'n_missing',
      nodes: [],
      edges: [],
      warnings: [],
    };
    expect(layoutGraph(empty, measure)).toEqual({ ok: false });
  });

  it('falls back to { ok: false } when an edge names a target that matches no node and is not "done"', () => {
    const broken: GraphView = {
      graph_id: 'gr_broken',
      name: 'broken',
      enabled: true,
      entry_node_id: 'n_a',
      nodes: [{ node_id: 'n_a', name: 'a', executor: 'runner', session: 'fresh', judged_by: 'worker', choices: [] }],
      edges: [{ from_node_id: 'n_a', choice_id: 'c_x', to_node_name: 'nonexistent' }],
      warnings: [],
    };
    expect(layoutGraph(broken, measure)).toEqual({ ok: false });
  });

  it('widens the overall bounding box to fit a self-loop arc + label even with a long node id/label', () => {
    const longLoop: GraphView = {
      graph_id: 'gr_long',
      name: 'long-loop',
      enabled: true,
      entry_node_id: 'n_01KXTHDJ3AAM439JWADAAPCGP1',
      nodes: [
        {
          node_id: 'n_01KXTHDJ3AAM439JWADAAPCGP1',
          name: 'a-node-with-a-very-long-identifier',
          executor: 'runner',
          session: 'fresh',
          judged_by: 'worker',
          choices: [{ choice_id: 'cho_01KXTHDJ3AAM439JWADAAPCGP1', name: 'a-very-long-retry-choice-name', description: '' }],
        },
      ],
      edges: [
        {
          from_node_id: 'n_01KXTHDJ3AAM439JWADAAPCGP1',
          choice_id: 'cho_01KXTHDJ3AAM439JWADAAPCGP1',
          to_node_name: 'a-node-with-a-very-long-identifier',
        },
      ],
      warnings: [],
    };
    const outcome = layoutGraph(longLoop, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;

    expect(outcome.graph.selfLoops).toHaveLength(1);
    const loop = outcome.graph.selfLoops[0];
    const arcExtentX = loop.label.x + loop.label.width / 2;
    const arcExtentY = loop.label.y + loop.label.height / 2;
    // The overall SVG bounding box must reach at least as far as the self-loop's
    // arc + label — a long label must not clip against the viewBox (bug fixed here).
    expect(outcome.graph.width).toBeGreaterThanOrEqual(arcExtentX);
    expect(outcome.graph.height).toBeGreaterThanOrEqual(arcExtentY);
  });

  it('falls back to { ok: false } when a node has more than one self-loop (the spike\'s stated limitation)', () => {
    const doubleLoop: GraphView = {
      graph_id: 'gr_double',
      name: 'double',
      enabled: true,
      entry_node_id: 'n_a',
      nodes: [{ node_id: 'n_a', name: 'a', executor: 'runner', session: 'fresh', judged_by: 'worker', choices: [] }],
      edges: [
        { from_node_id: 'n_a', choice_id: 'c_x', to_node_name: 'a' },
        { from_node_id: 'n_a', choice_id: 'c_y', to_node_name: 'a' },
      ],
      warnings: [],
    };
    expect(layoutGraph(doubleLoop, measure)).toEqual({ ok: false });
  });
});
