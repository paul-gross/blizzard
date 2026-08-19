import { ChangeDetectionStrategy, Component, InjectionToken, computed, inject, input, output } from '@angular/core';

import type { GraphView } from '../api/hub';
import { type DiagramSelection, endpointNodeIds, incidentEdgeIds } from './graph-diagram-selection';
import { GraphDiagramNodeShape } from './graph-diagram-node-shape';
import { GraphDiagramStart } from './graph-diagram-start';
import { type LaidOutEdge, type LaidOutSelfLoop, type LayoutOutcome, type TextMeasurer, layoutGraph } from './graph-layout';
import { GRAPH_TEXT_MEASURER } from './graph-text-measurer';

/** Layout seam: defaults to the real dagre-backed {@link layoutGraph}, overridable
 * in tests so `graph-diagram.spec.ts` can render from a canned {@link LayoutOutcome}
 * without depending on dagre's actual coordinates (mirrors the `EVENT_SOURCE_FACTORY`
 * injectable-seam pattern already used for SSE, `sse.service.ts`). */
export const GRAPH_LAYOUT = new InjectionToken<(graph: GraphView, measure: TextMeasurer) => LayoutOutcome>(
  'fleet.GRAPH_LAYOUT',
  { providedIn: 'root', factory: () => layoutGraph },
);

/**
 * The graph diagram — a static SVG DAG rendered from one immutable `GraphView`,
 * mounted above `graph-detail.ts`'s structured table (the ever-present fallback
 * surface). Layout runs once per input graph via `computed()` (spike #71: no live
 * re-layout, no pan/zoom in v1 — horizontal overflow scrolls in `.diagram-scroll`);
 * a layout failure or degenerate graph (see {@link layoutGraph}) shows an
 * unobtrusive notice instead of the diagram, never a broken page.
 *
 * The `.node-name` / `.node-badge` / `.node-meta` / `.edge-label` rules below are
 * mirrored — size, weight, family and tracking — by `graph-text-measurer.ts`, which
 * sizes every box around them. Change the two together or boxes size to type this
 * component does not draw (issue #157).
 *
 * Colors are CSS classes bound to `tokens.css` custom properties (`--cyan`,
 * `--amber`, `--red`, `--green`, `--label-dim`), never baked into SVG attributes —
 * the spike explicitly calls out the prototype's re-render-on-theme bug
 * (`spike71/part2.html`) as the thing to avoid: a theme switch here re-styles
 * without recomputing layout.
 *
 * Selectable and **fully controlled**: `selection` in, `selectionChange` out. This
 * component never holds its own copy of "what is selected" — `graph-diagram-view.ts`
 * is the sole owner, so the diagram and the detail pane beside it can't drift. Each
 * edge and self-loop carries an invisible companion `.edge-hit` path (same `d`, a
 * fat transparent stroke, `pointer-events: stroke`) so a click registers near the
 * curve rather than only exactly on its 2px visible stroke; the visible path is
 * `pointer-events: none` so it never steals the hit. The `<svg>` root's own click
 * clears the selection — every node/edge click stops propagation before it gets
 * there.
 *
 * The `<svg>` keeps `role="img"`, so its subtree is presentational to assistive
 * tech and the click targets here are a pointer affordance only — a deliberate
 * choice, not an oversight: `graph-detail.ts`'s ever-present structured table
 * covers the edges/choices list for keyboard and screen-reader access, but a
 * node's prompt/judgement text and an edge's prompt addendum live only behind
 * this diagram's node/edge selection (issue #208). Making the diagram a focusable
 * widget tree (roving tabindex, `role="application"`, Enter/Space) is real work
 * the issue this shipped under did not ask for.
 */
@Component({
  selector: 'fleet-graph-diagram',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GraphDiagramNodeShape, GraphDiagramStart],
  templateUrl: './graph-diagram.html',
  styleUrl: './graph-diagram.css',
})
export class GraphDiagram {
  /** The already-fetched graph to render — no re-fetch here (`bzh:generated-client`);
   * `graph-detail.ts` passes in the same `GraphView` its structured table already
   * holds. */
  readonly graph = input.required<GraphView>();

  /** The current selection, fully controlled by the parent — this component never
   * mutates its own copy, only renders whichever selection it is handed. */
  readonly selection = input<DiagramSelection | null>(null);
  /** Emits what the user clicked: a node, an edge (or self-loop), or `null` on an
   * empty-canvas click. The parent owns applying it back via `selection`. */
  readonly selectionChange = output<DiagramSelection | null>();

  private readonly layoutFn = inject(GRAPH_LAYOUT);
  private readonly measure = inject(GRAPH_TEXT_MEASURER);

  protected readonly outcome = computed<LayoutOutcome>(() => this.layoutFn(this.graph(), this.measure));

  protected isNodeSelected(nodeId: string): boolean {
    const s = this.selection();
    return s !== null && s.kind === 'node' && s.nodeId === nodeId;
  }

  /** Whether `nodeId` is an endpoint of the currently selected edge. */
  protected isNodeIncident(nodeId: string): boolean {
    return endpointNodeIds(this.selection()).includes(nodeId);
  }

  protected isEdgeSelected(edgeId: string): boolean {
    const s = this.selection();
    return s !== null && s.kind === 'edge' && s.edgeId === edgeId;
  }

  /** Whether `edgeId` (or self-loop id) is incident to the currently selected node. */
  protected isEdgeIncident(edgeId: string): boolean {
    const s = this.selection();
    if (s === null || s.kind !== 'node') return false;
    const o = this.outcome();
    if (!o.ok) return false;
    return incidentEdgeIds(o.graph, s.nodeId).includes(edgeId);
  }

  protected selectNode(nodeId: string, event: MouseEvent): void {
    event.stopPropagation();
    this.selectionChange.emit({ kind: 'node', nodeId });
  }

  protected selectEdge(edge: LaidOutEdge, event: MouseEvent): void {
    event.stopPropagation();
    this.selectionChange.emit({
      kind: 'edge',
      edgeId: edge.id,
      fromNodeId: edge.fromNodeId,
      toNodeId: edge.toNodeId,
      choiceId: edge.choiceId,
      edgeKind: edge.kind,
    });
  }

  /** A self-loop is a retry edge from a node to itself — its `kind` is always
   * `'retry'` by construction ({@link resolveEdges} in `graph-layout.ts`), so unlike
   * {@link selectEdge} there is no `kind` field on {@link LaidOutSelfLoop} to read. */
  protected selectSelfLoop(loop: LaidOutSelfLoop, event: MouseEvent): void {
    event.stopPropagation();
    this.selectionChange.emit({
      kind: 'edge',
      edgeId: loop.id,
      fromNodeId: loop.nodeId,
      toNodeId: loop.nodeId,
      choiceId: loop.choiceId,
      edgeKind: 'retry',
    });
  }

  protected clearSelection(): void {
    this.selectionChange.emit(null);
  }
}
