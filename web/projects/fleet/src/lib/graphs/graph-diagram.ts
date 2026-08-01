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
  template: `
    <div class="diagram-root" data-testid="graph-diagram">
      @if (outcome(); as o) {
        @if (o.ok) {
          <div class="diagram-scroll" data-testid="graph-diagram-scroll">
            <!-- role="img" keeps this subtree presentational to assistive tech (see the
                 class doc's a11y section) — a pointer-only clear affordance, not a
                 keyboard gap; the structured table below is the accessible path. -->
            <!-- eslint-disable-next-line @angular-eslint/template/click-events-have-key-events -->
            <svg
              [attr.viewBox]="'0 0 ' + o.graph.width + ' ' + o.graph.height"
              [attr.width]="o.graph.width"
              [attr.height]="o.graph.height"
              role="img"
              [attr.aria-label]="'Diagram of graph ' + graph().graph_id"
              data-testid="graph-diagram-svg"
              (click)="clearSelection()"
            >
              <defs>
                <marker
                  id="graph-diagram-arrow-advance"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" class="arrow-advance" />
                </marker>
                <marker
                  id="graph-diagram-arrow-retry"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" class="arrow-retry" />
                </marker>
              </defs>

              @for (edge of o.graph.edges; track edge.id) {
                <g
                  class="edge-group"
                  data-testid="graph-diagram-edge"
                  [attr.data-edge-kind]="edge.kind"
                  [attr.data-selected]="isEdgeSelected(edge.id) ? 'true' : null"
                  [attr.data-incident]="isEdgeIncident(edge.id) ? 'true' : null"
                  [class.selected]="isEdgeSelected(edge.id)"
                  [class.incident]="isEdgeIncident(edge.id)"
                  (click)="selectEdge(edge, $event)"
                >
                  <path
                    [attr.d]="edge.path"
                    [class]="'edge edge-' + edge.kind"
                    [attr.marker-end]="'url(#graph-diagram-arrow-' + edge.kind + ')'"
                  />
                  <path [attr.d]="edge.path" class="edge-hit" data-testid="graph-diagram-edge-hit" />
                  @if (edge.label; as label) {
                    <rect
                      class="edge-label-bg"
                      [attr.x]="label.x - label.width / 2"
                      [attr.y]="label.y - label.height / 2"
                      [attr.width]="label.width"
                      [attr.height]="label.height"
                      rx="4"
                    />
                    <text
                      [class]="'edge-label edge-label-' + edge.kind"
                      [attr.x]="label.x"
                      [attr.y]="label.y + 3.5"
                      text-anchor="middle"
                      data-testid="graph-diagram-edge-label"
                    >
                      {{ label.text }}
                    </text>
                  }
                </g>
              }

              @for (loop of o.graph.selfLoops; track loop.id) {
                <g
                  class="edge-group"
                  data-testid="graph-diagram-self-loop"
                  [attr.data-node-id]="loop.nodeId"
                  [attr.data-selected]="isEdgeSelected(loop.id) ? 'true' : null"
                  [attr.data-incident]="isEdgeIncident(loop.id) ? 'true' : null"
                  [class.selected]="isEdgeSelected(loop.id)"
                  [class.incident]="isEdgeIncident(loop.id)"
                  (click)="selectSelfLoop(loop, $event)"
                >
                  <path [attr.d]="loop.path" class="edge edge-retry" marker-end="url(#graph-diagram-arrow-retry)" />
                  <path [attr.d]="loop.path" class="edge-hit" data-testid="graph-diagram-edge-hit" />
                  <rect
                    class="edge-label-bg"
                    [attr.x]="loop.label.x - loop.label.width / 2"
                    [attr.y]="loop.label.y - loop.label.height / 2"
                    [attr.width]="loop.label.width"
                    [attr.height]="loop.label.height"
                    rx="4"
                  />
                  <text
                    class="edge-label edge-label-retry"
                    [attr.x]="loop.label.x"
                    [attr.y]="loop.label.y + 3.5"
                    text-anchor="middle"
                    data-testid="graph-diagram-edge-label"
                  >
                    {{ loop.label.text }}
                  </text>
                </g>
              }

              @for (node of o.graph.nodes; track node.id) {
                <g
                  fleetGraphDiagramNode
                  [node]="node"
                  [selected]="isNodeSelected(node.id)"
                  [incident]="isNodeIncident(node.id)"
                  (click)="selectNode(node.id, $event)"
                ></g>
              }

              @if (o.graph.start; as start) {
                <g fleetGraphDiagramStart [start]="start"></g>
              }

              @if (o.graph.done; as done) {
                <circle
                  class="done-sink"
                  [attr.cx]="done.x"
                  [attr.cy]="done.y"
                  [attr.r]="done.r"
                  data-testid="graph-diagram-done"
                />
                <text class="done-label" [attr.x]="done.x" [attr.y]="done.y + 4" text-anchor="middle">DONE</text>
              }
            </svg>
          </div>
        } @else {
          <p class="fallback-notice" data-testid="graph-diagram-fallback">
            Diagram unavailable for this graph — see the structured view below.
          </p>
        }
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
    }
    .diagram-scroll {
      overflow-x: auto;
      overflow-y: hidden;
      border: 1px solid var(--bezel);
      background: var(--overlay-20);
    }
    svg {
      display: block;
    }
    .fallback-notice {
      margin: 0;
      padding: 6px 8px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
      border: 1px dashed var(--bezel);
    }
    .edge-group {
      cursor: pointer;
    }
    .edge {
      fill: none;
      stroke-width: 2.25;
      /* Hit-testing goes through the companion .edge-hit path below — the visible
         stroke never intercepts a click, so a thin curve doesn't force pixel-exact
         clicks. */
      pointer-events: none;
    }
    .edge-hit {
      fill: none;
      stroke: transparent;
      stroke-width: 14px;
      pointer-events: stroke;
    }
    .edge-group:hover .edge {
      stroke-width: 3;
    }
    .edge-group.selected .edge {
      stroke-width: 3.5;
    }
    .edge-group.incident .edge {
      stroke-width: 3;
    }
    .edge-advance {
      stroke: var(--green);
    }
    .edge-retry {
      stroke: var(--amber);
      stroke-dasharray: 6 4;
    }
    .arrow-advance {
      fill: var(--green);
    }
    .arrow-retry {
      fill: var(--amber);
    }
    .edge-label-bg {
      fill: var(--panel-deep);
    }
    .edge-group:hover .edge-label-bg {
      fill: var(--panel-hover);
    }
    .edge-group.selected .edge-label-bg {
      fill: var(--panel-hi);
    }
    .edge-label {
      font-family: var(--mono);
      font-size: 11px;
      text-anchor: middle;
    }
    .edge-label-advance {
      fill: var(--green);
    }
    .edge-label-retry {
      fill: var(--amber);
    }
    .done-sink {
      fill: var(--green-dim);
      stroke: var(--green);
      stroke-width: 1.5;
    }
    .done-label {
      fill: var(--text);
      font-family: var(--mono);
      font-size: 10px;
      font-weight: 700;
      text-anchor: middle;
    }
  `,
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
