import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { GraphEdgeView, GraphNodeView } from '../api/hub';
import { GraphDiagram } from './graph-diagram';
import { injectHubGraphQuery } from './graphs.query';

/** One outgoing edge, resolved against the choice it fires on (the choice lives on
 * the *source* node's `choices`, edges only carry the `choice_id`). */
interface ResolvedEdge {
  readonly edge: GraphEdgeView;
  readonly choiceName: string;
  readonly choiceDescription: string;
}

/**
 * The graph explorer's **detail** view — one minted graph's immutable structure,
 * rendered in full: the entry node, a node table (executor, session, judged-by,
 * retries, mode, checks, produces), every edge with the choice it fires on and its
 * prompt addendum, and each node's judgement/prompt text. Consumes
 * `injectHubGraphQuery` reactively over the `graphId` input, which the host page
 * binds to the `/graphs/:graphId` route param — refresh-safe and deep-linkable by
 * construction (`bzh:generated-client`; no hand-written fetch).
 *
 * Mounts `<fleet-graph-diagram>` above the node table — the static DAG render of
 * the same `GraphView` (no re-fetch); the table stays the ever-present fallback
 * surface, unaffected by a diagram-layout failure.
 */
@Component({
  selector: 'fleet-graph-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GraphDiagram],
  template: `
    <section class="panel graph-detail" aria-label="Graph detail" data-testid="graph-detail">
      @if (graphQuery.isPending()) {
        <p class="none" data-testid="graph-detail-loading">Loading graph…</p>
      } @else if (graphQuery.isError()) {
        <p class="none" data-testid="graph-detail-error">Unknown graph: {{ graphId() }}</p>
      } @else if (graph(); as g) {
        <div class="body" data-testid="graph-detail-body">
          <div class="panel-head">
            <span class="lbl">{{ g.name }}</span>
            <span class="gid" data-testid="graph-detail-graph-id">{{ g.graph_id }}</span>
          </div>
          <p class="entry" data-testid="graph-detail-entry">
            Entry node: <strong>{{ entryNodeName() }}</strong>
          </p>

          <fleet-graph-diagram [graph]="g" data-testid="graph-detail-diagram" />

          <table class="nodes" data-testid="graph-detail-nodes">
            <thead>
              <tr>
                <th>Node</th>
                <th>Executor</th>
                <th>Session</th>
                <th>Judged by</th>
                <th>Retries</th>
                <th>Mode</th>
                <th>Checks</th>
                <th>Produces</th>
              </tr>
            </thead>
            <tbody>
              @for (node of nodes(); track node.node_id) {
                <tr data-testid="graph-detail-node-row" [attr.data-node-id]="node.node_id">
                  <td class="nid">
                    {{ node.name }}
                    @if (node.node_id === g.entry_node_id) {
                      <span class="entry-badge" data-testid="graph-detail-entry-badge">entry</span>
                    }
                  </td>
                  <td>{{ node.executor }}</td>
                  <td>{{ node.session }}</td>
                  <td>{{ node.judged_by }}</td>
                  <td>{{ retriesLabel(node) }}</td>
                  <td>{{ node.mode ?? '—' }}</td>
                  <td>{{ listOrDash(node.checks) }}</td>
                  <td>{{ listOrDash(node.produces) }}</td>
                </tr>
              }
            </tbody>
          </table>

          <div class="section" data-testid="graph-detail-edges">
            <span class="lbl">Edges &amp; choices</span>
            @for (node of nodes(); track node.node_id) {
              @if (resolvedEdges(node).length > 0) {
                <div class="node-edges" data-testid="graph-detail-node-edges" [attr.data-node-id]="node.node_id">
                  <span class="from">{{ node.name }}</span>
                  <ul class="edge-list">
                    @for (resolved of resolvedEdges(node); track resolved.edge.choice_id) {
                      <li class="edge" data-testid="graph-detail-edge">
                        <span class="choice" data-testid="graph-detail-edge-choice">{{ resolved.choiceName }}</span>
                        <span class="arrow">→</span>
                        <span class="to" data-testid="graph-detail-edge-to">{{ resolved.edge.to_node_name }}</span>
                        @if (resolved.edge.prompt_addendum) {
                          <p class="addendum" data-testid="graph-detail-edge-addendum">
                            {{ resolved.edge.prompt_addendum }}
                          </p>
                        }
                      </li>
                    }
                  </ul>
                </div>
              }
            }
          </div>

          <div class="section" data-testid="graph-detail-prompts">
            <span class="lbl">Prompts</span>
            @for (node of nodes(); track node.node_id) {
              @if (node.prompt) {
                <div class="prompt-block" data-testid="graph-detail-prompt" [attr.data-node-id]="node.node_id">
                  <span class="node-name">{{ node.name }}</span>
                  <pre class="prompt-text" data-testid="graph-detail-prompt-text">{{ node.prompt }}</pre>
                </div>
              }
            }
          </div>
        </div>
      }
    </section>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      overflow-y: auto;
      font-family: var(--mono);
      font-size: var(--fs-base);
      color: var(--text);
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .panel {
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border: 1px solid var(--bezel);
      min-height: 0;
    }
    .none {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      padding: 6px 8px;
    }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.25);
    }
    .gid {
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .body {
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 8px;
    }
    .entry {
      margin: 0;
      font-size: var(--fs-sm);
      color: var(--label-dim);
    }
    .entry strong {
      color: var(--cyan);
    }
    table.nodes {
      width: 100%;
      border-collapse: collapse;
      font-size: var(--fs-xs);
    }
    table.nodes th,
    table.nodes td {
      border: 1px solid var(--line);
      padding: 3px 6px;
      text-align: left;
      vertical-align: top;
    }
    table.nodes th {
      color: var(--label);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      background: rgba(0, 0, 0, 0.25);
    }
    .nid {
      color: var(--cyan);
    }
    .entry-badge {
      margin-left: 6px;
      padding: 0 4px;
      border: 1px solid var(--amber-hi);
      color: var(--amber-hi);
      font-size: 0.85em;
      text-transform: uppercase;
    }
    .section {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .node-edges {
      border: 1px solid var(--line);
      padding: 4px 6px;
    }
    .from {
      color: var(--cyan);
      font-size: var(--fs-sm);
    }
    .edge-list {
      list-style: none;
      margin: 4px 0 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 3px;
    }
    .edge {
      font-size: var(--fs-xs);
    }
    .choice {
      color: var(--amber-hi);
    }
    .arrow {
      color: var(--label-dim);
      margin: 0 4px;
    }
    .addendum {
      margin: 2px 0 0;
      color: var(--label-dim);
      white-space: pre-wrap;
    }
    .prompt-block {
      border: 1px solid var(--line);
      padding: 4px 6px;
    }
    .node-name {
      color: var(--cyan);
      font-size: var(--fs-sm);
    }
    .prompt-text {
      margin: 4px 0 0;
      white-space: pre-wrap;
      font-family: var(--mono);
      font-size: var(--fs-xs);
    }
  `,
})
export class GraphDetail {
  /** The graph id to render, deep-linked from the `/graphs/:graphId` route param. */
  readonly graphId = input.required<string>();

  protected readonly graphQuery = injectHubGraphQuery(() => this.graphId());

  protected readonly graph = computed(() => this.graphQuery.data());

  protected readonly nodes = computed<readonly GraphNodeView[]>(() => this.graph()?.nodes ?? []);

  protected readonly entryNodeName = computed<string>(() => {
    const g = this.graph();
    if (!g) return '';
    return this.nodes().find((n) => n.node_id === g.entry_node_id)?.name ?? g.entry_node_id;
  });

  protected retriesLabel(node: GraphNodeView): string {
    if (node.retries_max === undefined || node.retries_max === null) return '—';
    const exhausted = node.retries_exhausted ? ` → ${node.retries_exhausted}` : '';
    return `${node.retries_max}${exhausted}`;
  }

  protected listOrDash(values: readonly string[] | undefined): string {
    return values && values.length > 0 ? values.join(', ') : '—';
  }

  /** This node's outgoing edges, each resolved against the matching choice on the
   * same node (edges only carry `choice_id`; the choice's name/description live on
   * the source node's `choices`). */
  protected resolvedEdges(node: GraphNodeView): readonly ResolvedEdge[] {
    const g = this.graph();
    if (!g) return [];
    const choices = node.choices ?? [];
    return (g.edges ?? [])
      .filter((edge) => edge.from_node_id === node.node_id)
      .map((edge) => {
        const choice = choices.find((c) => c.choice_id === edge.choice_id);
        return {
          edge,
          choiceName: choice?.name ?? edge.choice_id,
          choiceDescription: choice?.description ?? '',
        };
      });
  }
}
