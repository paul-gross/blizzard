import dagre from '@dagrejs/dagre';

import type { GraphView } from '../api/hub';
import { LABEL_HEIGHT, type TextMeasurer, labelBoxWidth, migrationBox, nodeBox } from './graph-box-sizing';
import { DONE_TERMINAL, GRAPH_TARGET_PREFIX, type EdgeKind, type EdgeTarget, type ResolvedEdge, resolveEdges } from './graph-edge-target';

export { type TextMeasurer };
// Re-exported so no consumer outside this module pair has to know the discriminated
// target type and its structural-kind sibling actually live in `graph-edge-target.ts`
// (split out for the `web:lint` line cap — see that module's own doc comment).
export { type EdgeKind, type EdgeTarget };

/**
 * The pure DAG-layout core for the graph diagram (`bzh:generated-client` — this
 * module introduces no API call; it only lays out a `GraphView` already fetched by
 * `graphs.query.ts`). Confines the `@dagrejs/dagre` dependency to one module (spike
 * #71's recommendation) and stays framework-light/DOM-free so it unit-tests without
 * a browser: `graph-diagram.ts` is the only caller, and it supplies a
 * {@link TextMeasurer} (canvas `measureText` in production, a stub in tests) so node
 * and label boxes size to their rendered text instead of a char-count estimate. A node
 * box therefore has no fixed size: its width follows the wider of its name row and its
 * meta line, and its height grows with the meta line's wrap (see {@link nodeBox}).
 *
 * Blizzard graphs are not DAGs (`spike71/recommendation.md`): a choice edge may
 * target its own node (a self-loop retry) or an earlier node (a back edge, e.g.
 * review's fail-back into build). dagre's internal cycle-breaking handles back
 * edges; self-loops are filtered out of the dagre input and drawn separately by
 * {@link LaidOutGraph.selfLoops} as manual side arcs, per the spike.
 *
 * A choice's `to:` also names a **third** kind of target beyond a node or the
 * `done` terminal: `graph:<name>`, a cross-graph migration (`bzh:migration-not-transition`,
 * `src/blizzard/hub/domain/graph.py`'s `GRAPH_TARGET_PREFIX`) that re-pins the chunk to
 * another graph entirely rather than transitioning it within this one. Each distinct
 * target graph name gets its own synthetic dagre sink, laid out alongside `done` —
 * {@link LaidOutGraph.migrations}.
 *
 * `LaidOutEdge` and `LaidOutSelfLoop` carry identity — endpoints and `choiceId` —
 * alongside their geometry: the component's selection feature needs to highlight a
 * node's incident edges and render an edge's source/target, and `resolveEdges`
 * (`graph-edge-target.ts`) already computes exactly that internally. Surfacing it
 * as additive readonly fields keeps this module pair the single owner of edge
 * resolution (`canon:one-owner`) instead of forking the rule into the component.
 */

const DONE_RADIUS = 24;
/** The synthetic source dagre lays out above the entry node (blizzard#207) — sized
 * to match {@link DONE_RADIUS} so the START and DONE circles read as a pair. */
const START_RADIUS = DONE_RADIUS;
/** Dagre graph-lib id for the synthetic start node/edge — never a real `node_id`
 * (those come from the domain as `n_<name>`), so it can't collide with one. */
const START_TERMINAL = '__start__';
const START_EDGE_NAME = 'start';
/** Dagre graph-lib id namespace for a migration sink — `graph:<name>` targets are
 * already namespaced by {@link GRAPH_TARGET_PREFIX} on the wire, so reusing it as
 * the dagre id can't collide with a real `node_id` (those come from the domain as
 * `n_<name>`) or with {@link DONE_TERMINAL}/{@link START_TERMINAL}. */
function migrationSinkId(targetGraph: string): string {
  return `${GRAPH_TARGET_PREFIX}${targetGraph}`;
}
/** Horizontal margin reserved so a self-loop's side arc doesn't clip the viewBox. */
const SELF_LOOP_MARGIN = 60;

export interface LaidOutNode {
  readonly id: string;
  readonly name: string;
  readonly executor: string;
  /** The meta line, already wrapped to the box: one entry per rendered line, empty
   * when the node has no meta at all. */
  readonly metaLines: readonly string[];
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface LaidOutLabel {
  readonly text: string;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface LaidOutEdge {
  readonly id: string;
  readonly kind: EdgeKind;
  /** An SVG path `d` attribute, already routed through dagre's control points. */
  readonly path: string;
  readonly label: LaidOutLabel | null;
  readonly fromNodeId: string;
  readonly target: EdgeTarget;
  readonly choiceId: string;
}

export interface LaidOutSelfLoop {
  /** Shares the `e<i>` id space with {@link LaidOutGraph.edges} — a self-loop is a
   * `ResolvedEdge` like any other, just routed through this side arc instead of dagre. */
  readonly id: string;
  readonly nodeId: string;
  readonly path: string;
  readonly label: LaidOutLabel;
  readonly choiceId: string;
}

export interface LaidOutDone {
  readonly x: number;
  readonly y: number;
  readonly r: number;
}

export interface LaidOutStart {
  readonly x: number;
  readonly y: number;
  readonly r: number;
  /** SVG path from the circle to the entry node's top edge, dagre-routed like any
   * other edge — the component draws it with the same arrowhead as an advance edge. */
  readonly path: string;
}

/** A migration sink's box — one per distinct `graph:<name>` target in `graph.edges`,
 * laid out by dagre like a node but rendered as a labelled exit pill, never a node
 * box. */
export interface LaidOutMigration {
  readonly targetGraph: string;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface LaidOutGraph {
  readonly width: number;
  readonly height: number;
  readonly nodes: readonly LaidOutNode[];
  readonly edges: readonly LaidOutEdge[];
  readonly selfLoops: readonly LaidOutSelfLoop[];
  /** `null` when no edge in the graph targets the reserved terminal. */
  readonly done: LaidOutDone | null;
  /** `null` only when `entry_node_id` names no node in `graph` — a degenerate graph
   * the component simply renders without a start indicator. */
  readonly start: LaidOutStart | null;
  /** One entry per distinct `graph:<name>` target among `graph.edges`, in first-seen
   * order — empty when the graph carries no migration edges. */
  readonly migrations: readonly LaidOutMigration[];
}

export type LayoutOutcome = { readonly ok: true; readonly graph: LaidOutGraph } | { readonly ok: false };

function selfLoopPath(x0: number, y0: number, y1: number, bulge: number): string {
  return `M ${x0} ${y0} C ${x0 + bulge} ${y0 - 10}, ${x0 + bulge} ${y1 + 10}, ${x0 + 4} ${y1}`;
}

/** Turns a dagre edge's routed `points` into the same quadratic-curve `d` string
 * every forward edge and the synthetic start connector render with. */
function curvedPath(points: readonly { x: number; y: number }[]): string {
  let d = `M ${points[0].x} ${points[0].y}`;
  for (let j = 1; j < points.length - 1; j++) {
    const mx = (points[j].x + points[j + 1].x) / 2;
    const my = (points[j].y + points[j + 1].y) / 2;
    d += ` Q ${points[j].x} ${points[j].y} ${mx} ${my}`;
  }
  const last = points[points.length - 1];
  d += ` L ${last.x} ${last.y}`;
  return d;
}

/** The dagre graph-lib id `target` routes through — a real node id, the shared
 * `done` sink, or a migration's own sink, one per distinct target graph name. */
function dagreTargetId(target: EdgeTarget): string {
  switch (target.kind) {
    case 'node':
      return target.nodeId;
    case 'done':
      return DONE_TERMINAL;
    case 'graph':
      return migrationSinkId(target.targetGraph);
  }
}

/**
 * Lays out one immutable graph once (spike #71: no live re-layout, no pan/zoom in
 * v1). Returns `{ ok: false }` — never throws — on a degenerate graph (no nodes, an
 * edge naming an unknown target, more than one self-loop on a node — the spike's
 * stated ≤1-per-node limitation) or if dagre itself throws; `graph-diagram.ts`
 * shows an unobtrusive fallback notice in that case and the structured table view
 * stays the fallback surface.
 */
export function layoutGraph(graph: GraphView, measure: TextMeasurer): LayoutOutcome {
  const nodes = graph.nodes ?? [];
  if (nodes.length === 0) return { ok: false };

  const nameToId = new Map(nodes.map((n) => [n.name, n.node_id]));
  const resolved = resolveEdges(graph, nameToId);
  if (resolved === null) return { ok: false };

  const selfLoopsByNode = new Map<string, ResolvedEdge>();
  for (const edge of resolved) {
    if (edge.target.kind === 'node' && edge.target.nodeId === edge.fromId) {
      if (selfLoopsByNode.has(edge.fromId)) return { ok: false }; // >1 self-loop per node: unsupported
      selfLoopsByNode.set(edge.fromId, edge);
    }
  }

  const usesDone = resolved.some((e) => e.target.kind === 'done');
  // First-seen order, one sink per distinct target graph name — mirrors the `done`
  // sink's "shared by every edge into it" shape, just keyed by name instead of a
  // single reserved terminal.
  const migrationNames: string[] = [];
  const seenMigrations = new Set<string>();
  for (const edge of resolved) {
    if (edge.target.kind === 'graph' && !seenMigrations.has(edge.target.targetGraph)) {
      seenMigrations.add(edge.target.targetGraph);
      migrationNames.push(edge.target.targetGraph);
    }
  }
  const hasEntry = nodes.some((n) => n.node_id === graph.entry_node_id);
  const boxes = new Map(nodes.map((n) => [n.node_id, nodeBox(n, measure)]));
  const migrationBoxes = new Map(migrationNames.map((name) => [name, migrationBox(name, measure)]));

  try {
    const g = new dagre.graphlib.Graph({ multigraph: true });
    g.setGraph({ rankdir: 'TB', nodesep: 46, ranksep: 64, edgesep: 24, marginx: 24, marginy: 16 });
    g.setDefaultEdgeLabel(() => ({}));

    for (const n of nodes) {
      const box = boxes.get(n.node_id)!;
      g.setNode(n.node_id, { width: box.width, height: box.height });
    }
    if (usesDone) g.setNode(DONE_TERMINAL, { width: DONE_RADIUS * 2, height: DONE_RADIUS * 2 });
    for (const name of migrationNames) {
      const box = migrationBoxes.get(name)!;
      g.setNode(migrationSinkId(name), { width: box.width, height: box.height });
    }
    if (hasEntry) {
      g.setNode(START_TERMINAL, { width: START_RADIUS * 2, height: START_RADIUS * 2 });
      g.setEdge(START_TERMINAL, graph.entry_node_id, {}, START_EDGE_NAME);
    }

    const forwardEdges = resolved.filter((e) => !(e.target.kind === 'node' && e.target.nodeId === e.fromId));
    for (const edge of forwardEdges) {
      const target = dagreTargetId(edge.target);
      const labelW = labelBoxWidth(edge.label, measure);
      g.setEdge(edge.fromId, target, { width: labelW, height: LABEL_HEIGHT, labelpos: 'c' }, edge.id);
    }

    dagre.layout(g);

    const laidOutNodes: LaidOutNode[] = nodes.map((n) => {
      const pos = g.node(n.node_id);
      const box = boxes.get(n.node_id)!;
      return {
        id: n.node_id,
        name: n.name,
        executor: n.executor,
        metaLines: box.metaLines,
        x: pos.x - box.width / 2,
        y: pos.y - box.height / 2,
        width: box.width,
        height: box.height,
      };
    });

    const laidOutEdges: LaidOutEdge[] = forwardEdges.map((edge) => {
      const target = dagreTargetId(edge.target);
      const e = g.edge(edge.fromId, target, edge.id);
      const d = curvedPath(e.points);
      const labelX = e['x'] as number | undefined;
      const labelY = e['y'] as number | undefined;
      const label: LaidOutLabel | null =
        labelX !== undefined && labelY !== undefined
          ? { text: edge.label, x: labelX, y: labelY, width: labelBoxWidth(edge.label, measure), height: LABEL_HEIGHT }
          : null;
      return { id: edge.id, kind: edge.kind, path: d, label, fromNodeId: edge.fromId, target: edge.target, choiceId: edge.choiceId };
    });

    const selfLoops: LaidOutSelfLoop[] = [...selfLoopsByNode.values()].map((edge) => {
      const n = g.node(edge.fromId);
      const x0 = n.x + boxes.get(edge.fromId)!.width / 2;
      const y0 = n.y - 12;
      const y1 = n.y + 12;
      const bulge = 44;
      const labelW = labelBoxWidth(edge.label, measure);
      return {
        id: edge.id,
        nodeId: edge.fromId,
        path: selfLoopPath(x0, y0, y1, bulge),
        label: { text: edge.label, x: x0 + bulge + 2, y: n.y, width: labelW, height: LABEL_HEIGHT },
        choiceId: edge.choiceId,
      };
    });

    const done: LaidOutDone | null = usesDone
      ? (() => {
          const dn = g.node(DONE_TERMINAL);
          return { x: dn.x, y: dn.y, r: DONE_RADIUS };
        })()
      : null;

    const migrations: LaidOutMigration[] = migrationNames.map((name) => {
      const pos = g.node(migrationSinkId(name));
      const box = migrationBoxes.get(name)!;
      return { targetGraph: name, x: pos.x - box.width / 2, y: pos.y - box.height / 2, width: box.width, height: box.height };
    });

    const start: LaidOutStart | null = hasEntry
      ? (() => {
          const sn = g.node(START_TERMINAL);
          const e = g.edge(START_TERMINAL, graph.entry_node_id, START_EDGE_NAME);
          return { x: sn.x, y: sn.y, r: START_RADIUS, path: curvedPath(e.points) };
        })()
      : null;

    // dagre only sizes `g.graph().width`/`height` around the nodes and forward
    // edges it laid out — the self-loop side arcs and their labels are drawn
    // manually (outside dagre's model, see the module doc) and can reach further
    // right/down than anything dagre knows about. Widen/heighten the overall
    // bounding box to the self-loops' actual extent so a long choice-name label
    // doesn't clip against the SVG viewBox.
    const selfLoopExtentX = selfLoops.reduce(
      (max, loop) => Math.max(max, loop.label.x + loop.label.width / 2 + SELF_LOOP_MARGIN),
      0,
    );
    const selfLoopExtentY = selfLoops.reduce((max, loop) => Math.max(max, loop.label.y + loop.label.height / 2), 0);

    return {
      ok: true,
      graph: {
        width: Math.max(g.graph().width! + SELF_LOOP_MARGIN, selfLoopExtentX),
        height: Math.max(g.graph().height!, selfLoopExtentY),
        nodes: laidOutNodes,
        edges: laidOutEdges,
        selfLoops,
        done,
        start,
        migrations,
      },
    };
  } catch {
    return { ok: false };
  }
}
