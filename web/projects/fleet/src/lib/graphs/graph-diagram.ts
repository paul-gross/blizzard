import { ChangeDetectionStrategy, Component, InjectionToken, computed, inject, input } from '@angular/core';

import type { GraphView } from '../api/hub';
import { type LayoutOutcome, type TextMeasurer, layoutGraph } from './graph-layout';

/**
 * Canvas-backed {@link TextMeasurer} — measures a string's rendered pixel width for
 * real, per the spike's "measured text, not char-count estimation" constraint. Falls
 * back to a fixed per-character estimate only if the runtime has no working 2D
 * canvas context (unsupported in practice for a real browser; this is the defensive
 * edge, not the intended path — jsdom under Vitest is exactly this edge, which is
 * why component specs stub {@link GRAPH_LAYOUT} directly rather than relying on this
 * measurer's output).
 */
function createCanvasTextMeasurer(): TextMeasurer {
  const canvas = document.createElement('canvas');
  // jsdom (the unit-test DOM) has no canvas backend and throws rather than
  // returning null — guard the same way as an unsupported runtime.
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas.getContext('2d');
  } catch {
    ctx = null;
  }
  const fonts: Record<string, string> = {
    name: '600 13px var(--mono, monospace)',
    badge: '700 10px var(--mono, monospace)',
    meta: '400 11px var(--mono, monospace)',
    label: '400 11px var(--mono, monospace)',
  };
  const fallbackCharWidth: Record<string, number> = { name: 8, badge: 7, meta: 6, label: 6.5 };
  return (text, kind) => {
    if (ctx) {
      ctx.font = fonts[kind];
      return ctx.measureText(text).width;
    }
    return text.length * fallbackCharWidth[kind];
  };
}

/** Layout seam: defaults to the real dagre-backed {@link layoutGraph}, overridable
 * in tests so `graph-diagram.spec.ts` can render from a canned {@link LayoutOutcome}
 * without depending on dagre's actual coordinates (mirrors the `EVENT_SOURCE_FACTORY`
 * injectable-seam pattern already used for SSE, `sse.service.ts`). */
export const GRAPH_LAYOUT = new InjectionToken<(graph: GraphView, measure: TextMeasurer) => LayoutOutcome>(
  'fleet.GRAPH_LAYOUT',
  { providedIn: 'root', factory: () => layoutGraph },
);

/** Text-measurer seam, overridable alongside {@link GRAPH_LAYOUT} for deterministic
 * specs; production default is {@link createCanvasTextMeasurer}. */
export const GRAPH_TEXT_MEASURER = new InjectionToken<TextMeasurer>('fleet.GRAPH_TEXT_MEASURER', {
  providedIn: 'root',
  factory: () => createCanvasTextMeasurer(),
});

/**
 * The graph diagram — a static SVG DAG rendered from one immutable `GraphView`,
 * mounted above `graph-detail.ts`'s structured table (the ever-present fallback
 * surface). Layout runs once per input graph via `computed()` (spike #71: no live
 * re-layout, no pan/zoom in v1 — horizontal overflow scrolls in `.diagram-scroll`);
 * a layout failure or degenerate graph (see {@link layoutGraph}) shows an
 * unobtrusive notice instead of the diagram, never a broken page.
 *
 * Colors are CSS classes bound to `tokens.css` custom properties (`--cyan`,
 * `--amber`, `--red`, `--green`, `--label-dim`), never baked into SVG attributes —
 * the spike explicitly calls out the prototype's re-render-on-theme bug
 * (`spike71/part2.html`) as the thing to avoid: a theme switch here re-styles
 * without recomputing layout.
 */
@Component({
  selector: 'fleet-graph-diagram',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="diagram-root" data-testid="graph-diagram">
      @if (outcome(); as o) {
        @if (o.ok) {
          <div class="diagram-scroll" data-testid="graph-diagram-scroll">
            <svg
              [attr.viewBox]="'0 0 ' + o.graph.width + ' ' + o.graph.height"
              [attr.width]="o.graph.width"
              [attr.height]="o.graph.height"
              role="img"
              [attr.aria-label]="'Diagram of graph ' + graph().graph_id"
              data-testid="graph-diagram-svg"
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
                <g data-testid="graph-diagram-edge" [attr.data-edge-kind]="edge.kind">
                  <path
                    [attr.d]="edge.path"
                    [class]="'edge edge-' + edge.kind"
                    [attr.marker-end]="'url(#graph-diagram-arrow-' + edge.kind + ')'"
                  />
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

              @for (loop of o.graph.selfLoops; track loop.nodeId) {
                <g data-testid="graph-diagram-self-loop" [attr.data-node-id]="loop.nodeId">
                  <path [attr.d]="loop.path" class="edge edge-retry" marker-end="url(#graph-diagram-arrow-retry)" />
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
                  data-testid="graph-diagram-node"
                  [attr.data-node-id]="node.id"
                  [class.entry]="node.isEntry"
                  [class.exec-hub]="node.executor === 'hub'"
                  [class.exec-runner]="node.executor !== 'hub'"
                >
                  @if (node.isEntry) {
                    <rect
                      class="entry-ring"
                      [attr.x]="node.x - 4"
                      [attr.y]="node.y - 4"
                      [attr.width]="node.width + 8"
                      [attr.height]="node.height + 8"
                      rx="12"
                      data-testid="graph-diagram-entry-ring"
                    />
                  }
                  <rect
                    class="node-box"
                    [attr.x]="node.x"
                    [attr.y]="node.y"
                    [attr.width]="node.width"
                    [attr.height]="node.height"
                    rx="9"
                  />
                  <rect class="node-stripe" [attr.x]="node.x" [attr.y]="node.y" width="4" [attr.height]="node.height" />
                  <text class="node-name" [attr.x]="node.x + 14" [attr.y]="node.y + 24" data-testid="graph-diagram-node-name">
                    {{ node.name }}
                  </text>
                  <text
                    class="node-badge"
                    [attr.x]="node.x + node.width - 8"
                    [attr.y]="node.y + 20"
                    text-anchor="end"
                    data-testid="graph-diagram-node-badge"
                  >
                    {{ node.executor.toUpperCase() }}
                  </text>
                  @if (node.metaText) {
                    <text class="node-meta" [attr.x]="node.x + 14" [attr.y]="node.y + 44">{{ node.metaText }}</text>
                  }
                </g>
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
      background: rgba(0, 0, 0, 0.2);
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
    .node-box {
      fill: var(--panel);
      stroke: var(--bezel-hi);
      stroke-width: 1.25;
    }
    .node-stripe {
      fill: var(--label-dim);
    }
    .exec-runner .node-stripe {
      fill: var(--cyan);
    }
    .exec-hub .node-stripe {
      fill: var(--amber);
    }
    .entry-ring {
      fill: none;
      stroke: var(--amber-hi);
      stroke-width: 2;
    }
    .node-name {
      fill: var(--text);
      font-family: var(--mono);
      font-size: 13px;
      font-weight: 600;
    }
    .node-badge {
      fill: var(--label-dim);
      font-family: var(--mono);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.06em;
    }
    .exec-runner .node-badge {
      fill: var(--cyan);
    }
    .exec-hub .node-badge {
      fill: var(--amber);
    }
    .node-meta {
      fill: var(--label);
      font-family: var(--mono);
      font-size: 11px;
    }
    .edge {
      fill: none;
      stroke-width: 2.25;
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

  private readonly layoutFn = inject(GRAPH_LAYOUT);
  private readonly measure = inject(GRAPH_TEXT_MEASURER);

  protected readonly outcome = computed<LayoutOutcome>(() => this.layoutFn(this.graph(), this.measure));
}
