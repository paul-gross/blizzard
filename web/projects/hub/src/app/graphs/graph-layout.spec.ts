import type { GraphNode, Graph } from './graph.domain';
import { META_LINE_HEIGHT } from './graph-box-sizing';
import { type TextMeasurer } from './graph-box-sizing';
import { layoutGraph } from './graph-layout';

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

const EDGE_DEFAULTS: Pick<Graph['edges'][number], 'promptAddendum' | 'targetGraph' | 'targetKind'> = {
  promptAddendum: null,
  targetGraph: null,
  targetKind: 'node',
};

/** Deterministic stand-in for canvas `measureText` — a fixed per-character width so
 * layout math is reproducible without a DOM (the production measurer lives in
 * `graph-diagram.ts` and is exercised there). */
const measure: TextMeasurer = (text) => text.length * 7;

/** Mirrors `default.yaml`'s shape (`bzh:generated-client` fixture: build -> review ->
 * deliver): a self-loop (`build` fail-back into itself), two back edges (`review`'s
 * fail into `build`, `deliver`'s conflict into `build`), and a `done` terminal. */
const DEFAULT_LIKE: Graph = {
  ...GRAPH_DEFAULTS,
  graphId: 'gr_default_v1',
  name: 'default-delivery',
  entryNodeId: 'n_build',
  nodes: [
    {
      ...NODE_DEFAULTS,
      nodeId: 'n_build',
      name: 'build',
      executor: 'runner',
      session: 'resume',
      judgedBy: 'worker',
      retriesMax: 2,
      choices: [
        { choiceId: 'c_pass', name: 'pass', description: '' },
        { choiceId: 'c_fail', name: 'fail', description: '' },
      ],
    },
    {
      ...NODE_DEFAULTS,
      nodeId: 'n_review',
      name: 'review',
      executor: 'runner',
      session: 'fresh',
      judgedBy: 'worker',
      retriesMax: 2,
      produces: ['review-findings'],
      choices: [
        { choiceId: 'c_pass2', name: 'pass', description: '' },
        { choiceId: 'c_fail2', name: 'fail', description: '' },
      ],
    },
    {
      ...NODE_DEFAULTS,
      nodeId: 'n_deliver',
      name: 'deliver',
      executor: 'hub',
      session: 'fresh',
      judgedBy: 'worker',
      choices: [
        { choiceId: 'c_landed', name: 'landed', description: '' },
        { choiceId: 'c_conflict', name: 'conflict', description: '' },
      ],
    },
  ],
  edges: [
    { ...EDGE_DEFAULTS, fromNodeId: 'n_build', choiceId: 'c_pass', toNodeName: 'review' },
    { ...EDGE_DEFAULTS, fromNodeId: 'n_build', choiceId: 'c_fail', toNodeName: 'build' },
    { ...EDGE_DEFAULTS, fromNodeId: 'n_review', choiceId: 'c_pass2', toNodeName: 'deliver' },
    { ...EDGE_DEFAULTS, fromNodeId: 'n_review', choiceId: 'c_fail2', toNodeName: 'build' },
    { ...EDGE_DEFAULTS, fromNodeId: 'n_deliver', choiceId: 'c_landed', toNodeName: 'done', targetKind: 'done' },
    { ...EDGE_DEFAULTS, fromNodeId: 'n_deliver', choiceId: 'c_conflict', toNodeName: 'build' },
  ],
};

describe('layoutGraph', () => {
  it('lays out a build-review-deliver shape: 3 node boxes, done sink, one self-loop extracted', () => {
    const outcome = layoutGraph(DEFAULT_LIKE, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;

    expect(outcome.graph.nodes).toHaveLength(3);
    expect(outcome.graph.nodes.map((n) => n.id).sort()).toEqual(['n_build', 'n_deliver', 'n_review']);
    expect(outcome.graph.done).not.toBeNull();
    expect(outcome.graph.start).not.toBeNull();

    // The self-loop (build -fail-> build) is filtered out of the dagre edge set and
    // surfaces only in `selfLoops`, never in `edges`.
    expect(outcome.graph.selfLoops).toHaveLength(1);
    expect(outcome.graph.selfLoops[0].nodeId).toBe('n_build');
    expect(outcome.graph.edges).toHaveLength(5);
    // Edges resolve their label to the choice's NAME, not the raw wire `choiceId`.
    expect(outcome.graph.edges.every((e) => e.label?.text !== 'c_fail')).toBe(true);
    expect(outcome.graph.edges.every((e) => !e.label?.text.startsWith('c_'))).toBe(true);
  });

  it('labels edges with the choice NAME resolved from the source node, not the raw choiceId', () => {
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

  it('carries edge identity — endpoints and choiceId — for a forward edge, a back edge, and a done-terminal edge', () => {
    const outcome = layoutGraph(DEFAULT_LIKE, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;

    const byId = (id: string) => outcome.graph.edges.find((e) => e.id === id);

    // e0: build -c_pass-> review (forward edge)
    expect(byId('e0')).toMatchObject({ fromNodeId: 'n_build', target: { kind: 'node', nodeId: 'n_review' }, choiceId: 'c_pass' });
    // e3: review -c_fail2-> build (back edge)
    expect(byId('e3')).toMatchObject({ fromNodeId: 'n_review', target: { kind: 'node', nodeId: 'n_build' }, choiceId: 'c_fail2' });
    // e4: deliver -c_landed-> done (done-terminal edge)
    expect(byId('e4')).toMatchObject({ fromNodeId: 'n_deliver', target: { kind: 'done' }, choiceId: 'c_landed' });
  });

  it("carries the self-loop's id in the same `e<i>` space as `edges`, plus its choiceId", () => {
    const outcome = layoutGraph(DEFAULT_LIKE, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;

    // e1: build -c_fail-> build (self-loop, filtered out of `edges`)
    expect(outcome.graph.selfLoops[0]).toMatchObject({ id: 'e1', nodeId: 'n_build', choiceId: 'c_fail' });
    expect(outcome.graph.edges.some((e) => e.id === 'e1')).toBe(false);
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
    const single: Graph = {
      ...GRAPH_DEFAULTS,
      graphId: 'gr_solo',
      name: 'solo',
      entryNodeId: 'n_only',
      nodes: [{ ...NODE_DEFAULTS, nodeId: 'n_only', name: 'only', executor: 'runner', session: 'fresh', judgedBy: 'worker', choices: [] }],
      edges: [],
    };
    const outcome = layoutGraph(single, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.graph.nodes).toHaveLength(1);
    expect(outcome.graph.edges).toHaveLength(0);
    expect(outcome.graph.selfLoops).toHaveLength(0);
    expect(outcome.graph.done).toBeNull();
    expect(outcome.graph.start).not.toBeNull();
  });

  it('lays out a start indicator positioned above the entry node, and none when entryNodeId names no node', () => {
    const withEntry = layoutGraph(DEFAULT_LIKE, measure);
    expect(withEntry.ok).toBe(true);
    if (!withEntry.ok) return;

    const entryNode = withEntry.graph.nodes.find((n) => n.id === 'n_build')!;
    const start = withEntry.graph.start!;
    expect(start).not.toBeNull();
    expect(start.y).toBeLessThan(entryNode.y);

    const noEntry = layoutGraph({ ...DEFAULT_LIKE, entryNodeId: 'n_missing' }, measure);
    expect(noEntry.ok).toBe(true);
    if (!noEntry.ok) return;
    expect(noEntry.graph.start).toBeNull();
  });

  it('falls back to { ok: false } for an empty graph (no nodes)', () => {
    const empty: Graph = {
      ...GRAPH_DEFAULTS,
      graphId: 'gr_empty',
      name: 'empty',
      entryNodeId: 'n_missing',
      nodes: [],
      edges: [],
    };
    expect(layoutGraph(empty, measure)).toEqual({ ok: false });
  });

  it('falls back to { ok: false } when an edge names a target that matches no node and is not "done"', () => {
    const broken: Graph = {
      ...GRAPH_DEFAULTS,
      graphId: 'gr_broken',
      name: 'broken',
      entryNodeId: 'n_a',
      nodes: [{ ...NODE_DEFAULTS, nodeId: 'n_a', name: 'a', executor: 'runner', session: 'fresh', judgedBy: 'worker', choices: [] }],
      edges: [{ ...EDGE_DEFAULTS, fromNodeId: 'n_a', choiceId: 'c_x', toNodeName: 'nonexistent' }],
    };
    expect(layoutGraph(broken, measure)).toEqual({ ok: false });
  });

  it('draws no edge for an escalate choice and shows no done sink for it', () => {
    const escalating: Graph = {
      ...GRAPH_DEFAULTS,
      graphId: 'gr_escalating',
      name: 'escalating',
      entryNodeId: 'n_a',
      nodes: [{ ...NODE_DEFAULTS, nodeId: 'n_a', name: 'a', executor: 'runner', session: 'fresh', judgedBy: 'worker', choices: [] }],
      edges: [{ ...EDGE_DEFAULTS, fromNodeId: 'n_a', choiceId: 'c_stuck', toNodeName: 'escalate', targetKind: 'escalate' }],
    };
    const outcome = layoutGraph(escalating, measure);
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.graph.edges).toHaveLength(0);
    expect(outcome.graph.selfLoops).toHaveLength(0);
    expect(outcome.graph.done).toBeNull();
  });

  it('skips an edge whose targetKind this build does not list, laying out the rest', () => {
    const gained: Graph = {
      ...DEFAULT_LIKE,
      edges: [
        ...DEFAULT_LIKE.edges,
        { ...EDGE_DEFAULTS, fromNodeId: 'n_review', choiceId: 'c_pass2', toNodeName: 'elsewhere', targetKind: 'portal' as never },
      ],
    };
    const result = layoutGraph(gained, measure);
    expect(result.ok).toBe(true);
    const baseline = layoutGraph(DEFAULT_LIKE, measure);
    if (!result.ok || !baseline.ok) throw new Error('expected both layouts to succeed');
    expect(result.graph.edges.length).toBe(baseline.graph.edges.length);
  });

  describe('migration targets', () => {
    /** One node with an edge to `done` and several edges to `graph:<name>`
     * cross-graph migration targets, all of which must resolve. */
    const TRIAGE_LIKE: Graph = {
      ...GRAPH_DEFAULTS,
      graphId: 'gr_triage',
      name: 'default-delivery',
      entryNodeId: 'n_triage',
      nodes: [
        {
          ...NODE_DEFAULTS,
          nodeId: 'n_triage',
          name: 'triage',
          executor: 'runner',
          session: 'fresh',
          judgedBy: 'worker',
          choices: [
            { choiceId: 'c_already_done', name: 'already-done', description: '' },
            { choiceId: 'c_basic', name: 'basic', description: '' },
            { choiceId: 'c_advanced', name: 'advanced', description: '' },
            { choiceId: 'c_harness', name: 'harness', description: '' },
          ],
        },
      ],
      edges: [
        { ...EDGE_DEFAULTS, fromNodeId: 'n_triage', choiceId: 'c_already_done', toNodeName: 'done', targetKind: 'done' },
        { ...EDGE_DEFAULTS, fromNodeId: 'n_triage', choiceId: 'c_basic', toNodeName: 'graph:bas-dwf', targetKind: 'graph', targetGraph: 'bas-dwf' },
        { ...EDGE_DEFAULTS, fromNodeId: 'n_triage', choiceId: 'c_advanced', toNodeName: 'graph:adv-dwf', targetKind: 'graph', targetGraph: 'adv-dwf' },
        { ...EDGE_DEFAULTS, fromNodeId: 'n_triage', choiceId: 'c_harness', toNodeName: 'graph:bas-hwf', targetKind: 'graph', targetGraph: 'bas-hwf' },
      ],
    };

    it('lays out ok: true for a default-delivery-shaped graph with done and migration edges', () => {
      const outcome = layoutGraph(TRIAGE_LIKE, measure);
      expect(outcome.ok).toBe(true);
    });

    it('resolves a `graph:<name>` edge to a graph-kind target carrying the parsed name, distinct from done', () => {
      const outcome = layoutGraph(TRIAGE_LIKE, measure);
      expect(outcome.ok).toBe(true);
      if (!outcome.ok) return;

      const byId = (id: string) => outcome.graph.edges.find((e) => e.id === id);
      expect(byId('e0')?.target).toEqual({ kind: 'done' });
      expect(byId('e1')?.target).toEqual({ kind: 'graph', targetGraph: 'bas-dwf' });
      expect(byId('e2')?.target).toEqual({ kind: 'graph', targetGraph: 'adv-dwf' });
      expect(byId('e3')?.target).toEqual({ kind: 'graph', targetGraph: 'bas-hwf' });
      // A migration always leaves the graph forward — it can never be a self-loop or
      // back edge, since it names no node index in this graph at all.
      expect(byId('e1')?.kind).toBe('advance');
    });

    it('lays out one migration sink per distinct target graph name, positioned like a node', () => {
      const outcome = layoutGraph(TRIAGE_LIKE, measure);
      expect(outcome.ok).toBe(true);
      if (!outcome.ok) return;

      expect(outcome.graph.migrations).toHaveLength(3);
      expect(outcome.graph.migrations.map((m) => m.targetGraph).sort()).toEqual(['adv-dwf', 'bas-dwf', 'bas-hwf']);
      for (const migration of outcome.graph.migrations) {
        expect(migration.width).toBeGreaterThan(0);
        expect(migration.height).toBeGreaterThan(0);
      }
    });

    it('collapses two edges into the same target graph name onto a single migration sink', () => {
      const shared: Graph = {
        ...TRIAGE_LIKE,
        nodes: [
          {
            ...TRIAGE_LIKE.nodes[0],
            choices: [
              ...TRIAGE_LIKE.nodes[0].choices,
              { choiceId: 'c_basic_2', name: 'basic-again', description: '' },
            ],
          },
        ],
        edges: [...TRIAGE_LIKE.edges, { ...EDGE_DEFAULTS, fromNodeId: 'n_triage', choiceId: 'c_basic_2', toNodeName: 'graph:bas-dwf', targetKind: 'graph', targetGraph: 'bas-dwf' }],
      };
      const outcome = layoutGraph(shared, measure);
      expect(outcome.ok).toBe(true);
      if (!outcome.ok) return;

      expect(outcome.graph.migrations.filter((m) => m.targetGraph === 'bas-dwf')).toHaveLength(1);
      expect(outcome.graph.edges.filter((e) => e.target.kind === 'graph' && e.target.targetGraph === 'bas-dwf')).toHaveLength(2);
    });

    it('falls back to { ok: false } for a malformed `graph:` target — empty name or a further colon', () => {
      const emptyName: Graph = {
        ...TRIAGE_LIKE,
        edges: [{ ...EDGE_DEFAULTS, fromNodeId: 'n_triage', choiceId: 'c_basic', toNodeName: 'graph:' }],
      };
      expect(layoutGraph(emptyName, measure)).toEqual({ ok: false });

      const extraColon: Graph = {
        ...TRIAGE_LIKE,
        edges: [{ ...EDGE_DEFAULTS, fromNodeId: 'n_triage', choiceId: 'c_basic', toNodeName: 'graph:bas:dwf' }],
      };
      expect(layoutGraph(extraColon, measure)).toEqual({ ok: false });
    });
  });

  it('widens the overall bounding box to fit a self-loop arc + label even with a long node id/label', () => {
    const longLoop: Graph = {
      ...GRAPH_DEFAULTS,
      graphId: 'gr_long',
      name: 'long-loop',
      entryNodeId: 'n_01KXTHDJ3AAM439JWADAAPCGP1',
      nodes: [
        {
          ...NODE_DEFAULTS,
          nodeId: 'n_01KXTHDJ3AAM439JWADAAPCGP1',
          name: 'a-node-with-a-very-long-identifier',
          executor: 'runner',
          session: 'fresh',
          judgedBy: 'worker',
          choices: [{ choiceId: 'cho_01KXTHDJ3AAM439JWADAAPCGP1', name: 'a-very-long-retry-choice-name', description: '' }],
        },
      ],
      edges: [
        {
          ...EDGE_DEFAULTS,
          fromNodeId: 'n_01KXTHDJ3AAM439JWADAAPCGP1',
          choiceId: 'cho_01KXTHDJ3AAM439JWADAAPCGP1',
          toNodeName: 'a-node-with-a-very-long-identifier',
        },
      ],
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

  describe('meta line', () => {
    /** A one-node graph whose single node carries the given overrides — the smallest
     * shape that isolates the box derivation from dagre's edge routing. */
    function soloGraph(overrides: Partial<GraphNode>): Graph {
      return {
        ...GRAPH_DEFAULTS,
        graphId: 'gr_solo',
        name: 'solo',
        entryNodeId: 'n_only',
        nodes: [
          {
            ...NODE_DEFAULTS,
            nodeId: 'n_only',
            name: 'only',
            executor: 'runner',
            session: 'fresh',
            judgedBy: 'worker',
            choices: [],
            ...overrides,
          },
        ],
        edges: [],
      };
    }

    function soloNode(overrides: Partial<GraphNode>) {
      const outcome = layoutGraph(soloGraph(overrides), measure);
      if (!outcome.ok) throw new Error('expected a layout');
      return outcome.graph.nodes[0];
    }

    it('renders a targeted resume in its authored `resume:<node>` form', () => {
      expect(soloNode({ session: 'resume', sessionSource: 'code' }).metaLines).toEqual(['resume:code']);
    });

    it('renders a bare resume and a fresh node without a target suffix', () => {
      expect(soloNode({ session: 'resume' }).metaLines).toEqual(['resume']);
      expect(soloNode({ session: 'fresh' }).metaLines).toEqual(['fresh']);
    });

    it('keeps a short meta on one line at the base box height', () => {
      const node = soloNode({ session: 'resume', retriesMax: 2 });
      expect(node.metaLines).toEqual(['resume · retries 2']);
      expect(node.height).toBe(60);
    });

    it('wraps a long meta onto further lines, growing the box height per extra line', () => {
      // Seven produces names at 7px/char blow well past the 420px max box width, so the
      // `→ …` segment cannot share a line with the leading session/retries segments.
      const node = soloNode({
        session: 'resume',
        sessionSource: 'code',
        retriesMax: 2,
        produces: Array.from({ length: 7 }, (_, i) => (`artifact-number-${i}`)),
      });

      expect(node.metaLines.length).toBeGreaterThan(1);
      expect(node.metaLines[0]).toBe('resume:code · retries 2');
      // Nothing is dropped by the wrap — the joined lines are the unwrapped meta.
      expect(node.metaLines.join(' · ')).toBe(
        `resume:code · retries 2 · → ${Array.from({ length: 7 }, (_, i) => `artifact-number-${i}`).join(', ')}`,
      );
      expect(node.height).toBe(60 + (node.metaLines.length - 1) * META_LINE_HEIGHT);
    });

    it('sizes the box to its widest wrapped line, never to the unwrapped meta', () => {
      const node = soloNode({
        session: 'resume',
        retriesMax: 2,
        produces: ['plan', 'retrospective', 'review-findings'],
      });

      const unwrapped = measure(node.metaLines.join(' · '), 'meta');
      const widest = node.metaLines.reduce((max, line) => Math.max(max, measure(line, 'meta')), 0);
      expect(widest).toBeLessThan(unwrapped);
      // 28px is the meta row's horizontal padding (14 either side).
      expect(node.width).toBeGreaterThanOrEqual(widest + 28);
      expect(node.width).toBeLessThan(unwrapped + 28);
    });

    it('lets one unsplittable segment wider than the max width widen the box rather than clip', () => {
      const long = `x`.repeat(120);
      const node = soloNode({ session: 'fresh', produces: [long] });

      expect(node.metaLines).toEqual(['fresh', `→ ${long}`]);
      expect(node.width).toBeGreaterThanOrEqual(measure(`→ ${long}`, 'meta') + 28);
    });
  });

  it('falls back to { ok: false } when a node has more than one self-loop (the spike\'s stated limitation)', () => {
    const doubleLoop: Graph = {
      ...GRAPH_DEFAULTS,
      graphId: 'gr_double',
      name: 'double',
      entryNodeId: 'n_a',
      nodes: [{ ...NODE_DEFAULTS, nodeId: 'n_a', name: 'a', executor: 'runner', session: 'fresh', judgedBy: 'worker', choices: [] }],
      edges: [
        { ...EDGE_DEFAULTS, fromNodeId: 'n_a', choiceId: 'c_x', toNodeName: 'a' },
        { ...EDGE_DEFAULTS, fromNodeId: 'n_a', choiceId: 'c_y', toNodeName: 'a' },
      ],
    };
    expect(layoutGraph(doubleLoop, measure)).toEqual({ ok: false });
  });
});
