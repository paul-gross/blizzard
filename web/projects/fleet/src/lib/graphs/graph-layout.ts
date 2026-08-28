import dagre from '@dagrejs/dagre';

import type { GraphView } from '../api/hub';
import { LABEL_HEIGHT, type TextMeasurer, labelBoxWidth, nodeBox } from './graph-box-sizing';

export { type TextMeasurer };

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
 * `LaidOutEdge` and `LaidOutSelfLoop` carry identity — endpoints and `choiceId` —
 * alongside their geometry: the component's selection feature needs to highlight a
 * node's incident edges and render an edge's source/target, and `resolveEdges`
 * already computes exactly that internally. Surfacing it as additive readonly
 * fields keeps this module the single owner of edge resolution (`canon:one-owner`)
 * instead of forking the rule into the component.
 */

/** A node's declaration in `graph.nodes` names the terminal a choice can point at
 * instead of a node — the domain's `RESERVED_TERMINAL`
 * (`src/blizzard/hub/domain/graph.py`). Duplicated here (not a backend import) since
 * the wire model carries it as a plain string, not a discriminated value. */
const DONE_TERMINAL = 'done';

const DONE_RADIUS = 24;
/** The synthetic source dagre lays out above the entry node (blizzard#207) — sized
 * to match {@link DONE_RADIUS} so the START and DONE circles read as a pair. */
const START_RADIUS = DONE_RADIUS;
/** Dagre graph-lib id for the synthetic start node/edge — never a real `node_id`
 * (those come from the domain as `n_<name>`), so it can't collide with one. */
const START_TERMINAL = '__start__';
const START_EDGE_NAME = 'start';
/** Horizontal margin reserved so a self-loop's side arc doesn't clip the viewBox. */
const SELF_LOOP_MARGIN = 60;

/** An edge's derived semantic kind — purely structural, since the wire model
 * carries no `kind` field: an edge to the reserved `done` terminal (or any
 * forward-pointing edge) is `advance`; a self-loop or a back edge (target
 * declared no later than its source) is `retry`. */
export type EdgeKind = 'advance' | 'retry';

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
  /** `null` when the edge targets the reserved `done` terminal. */
  readonly toNodeId: string | null;
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
}

export type LayoutOutcome = { readonly ok: true; readonly graph: LaidOutGraph } | { readonly ok: false };

interface ResolvedEdge {
  readonly id: string;
  readonly fromId: string;
  /** `null` when the edge targets the reserved `done` terminal. */
  readonly toId: string | null;
  readonly kind: EdgeKind;
  readonly label: string;
  readonly choiceId: string;
}

/** Resolves every edge's target node id (or `null` for the reserved `done`
 * terminal) and its structural kind. Returns `null` if an edge names a target that
 * matches no node and isn't the reserved terminal — a degenerate graph the caller
 * falls back on rather than mis-render.
 *
 * Only edges actually present in `graph.edges` are laid out here — the runtime's
 * machinery-default edges (e.g. a `deliver` node's implicit `landed→done` /
 * `conflict→entry`) are never part of the wire `GraphView` and are intentionally
 * *not* synthesized for the diagram, so the `done` sink (and any edge into it)
 * only renders when a real authored edge targets `done`. */
function resolveEdges(graph: GraphView, nameToId: ReadonlyMap<string, string>): ResolvedEdge[] | null {
  const indexById = new Map(graph.nodes?.map((n, i) => [n.node_id, i]) ?? []);
  const nodeById = new Map(graph.nodes?.map((n) => [n.node_id, n]) ?? []);
  const resolved: ResolvedEdge[] = [];
  for (const [i, edge] of (graph.edges ?? []).entries()) {
    // The choice's name lives on the *source* node's `choices`, not the edge — the
    // edge only carries `choice_id` (mirrors `graph-detail.ts`'s `resolvedEdges`).
    const choice = nodeById.get(edge.from_node_id)?.choices?.find((c) => c.choice_id === edge.choice_id);
    const label = choice?.name ?? edge.choice_id;
    if (edge.to_node_name === DONE_TERMINAL) {
      resolved.push({ id: `e${i}`, fromId: edge.from_node_id, toId: null, kind: 'advance', label, choiceId: edge.choice_id });
      continue;
    }
    const toId = nameToId.get(edge.to_node_name);
    if (toId === undefined) return null;
    const fromIndex = indexById.get(edge.from_node_id);
    const toIndex = indexById.get(toId);
    if (fromIndex === undefined || toIndex === undefined) return null;
    const isSelfLoop = toId === edge.from_node_id;
    const isBackEdge = !isSelfLoop && toIndex <= fromIndex;
    const kind: EdgeKind = isSelfLoop || isBackEdge ? 'retry' : 'advance';
    resolved.push({ id: `e${i}`, fromId: edge.from_node_id, toId, kind, label, choiceId: edge.choice_id });
  }
  return resolved;
}

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
    if (edge.toId === edge.fromId) {
      if (selfLoopsByNode.has(edge.fromId)) return { ok: false }; // >1 self-loop per node: unsupported
      selfLoopsByNode.set(edge.fromId, edge);
    }
  }

  const usesDone = resolved.some((e) => e.toId === null);
  const hasEntry = nodes.some((n) => n.node_id === graph.entry_node_id);
  const boxes = new Map(nodes.map((n) => [n.node_id, nodeBox(n, measure)]));

  try {
    const g = new dagre.graphlib.Graph({ multigraph: true });
    g.setGraph({ rankdir: 'TB', nodesep: 46, ranksep: 64, edgesep: 24, marginx: 24, marginy: 16 });
    g.setDefaultEdgeLabel(() => ({}));

    for (const n of nodes) {
      const box = boxes.get(n.node_id)!;
      g.setNode(n.node_id, { width: box.width, height: box.height });
    }
    if (usesDone) g.setNode(DONE_TERMINAL, { width: DONE_RADIUS * 2, height: DONE_RADIUS * 2 });
    if (hasEntry) {
      g.setNode(START_TERMINAL, { width: START_RADIUS * 2, height: START_RADIUS * 2 });
      g.setEdge(START_TERMINAL, graph.entry_node_id, {}, START_EDGE_NAME);
    }

    const forwardEdges = resolved.filter((e) => e.toId !== e.fromId);
    for (const edge of forwardEdges) {
      const target = edge.toId ?? DONE_TERMINAL;
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
      const target = edge.toId ?? DONE_TERMINAL;
      const e = g.edge(edge.fromId, target, edge.id);
      const d = curvedPath(e.points);
      const labelX = e['x'] as number | undefined;
      const labelY = e['y'] as number | undefined;
      const label: LaidOutLabel | null =
        labelX !== undefined && labelY !== undefined
          ? { text: edge.label, x: labelX, y: labelY, width: labelBoxWidth(edge.label, measure), height: LABEL_HEIGHT }
          : null;
      return { id: edge.id, kind: edge.kind, path: d, label, fromNodeId: edge.fromId, toNodeId: edge.toId, choiceId: edge.choiceId };
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
      },
    };
  } catch {
    return { ok: false };
  }
}
