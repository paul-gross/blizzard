import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import type { GraphEdgeView, GraphNodeView, GraphSessionView } from '../api/hub';
import { hasPermission, injectMeQuery } from '../auth/me.query';
import { errorMessage } from '../error-message';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitButton } from '../kit/kit-button';
import { asyncState } from '../query-state';
import { GraphDiagramView } from './graph-diagram-view';
import { injectGraphLifecycleMutation } from './graph-lifecycle.mutations';
import { GraphNodeTable } from './graph-node-table';
import { GraphSessionTable } from './graph-session-table';
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
 * retries, mode, checks, produces), the graph-level session declarations (issue #144),
 * every edge with the choice it fires on and its prompt addendum, and each node's
 * judgement/prompt text. Consumes
 * `injectHubGraphQuery` reactively over the `graphId` input, which the host page
 * binds to the `/graphs/:graphId` route param — refresh-safe and deep-linkable by
 * construction (`bzh:generated-client`; no hand-written fetch).
 *
 * Mounts `<fleet-graph-diagram-view>` above the node table — the selectable DAG
 * render of the same `GraphView` plus its detail pane (blizzard#159), no re-fetch;
 * the table stays the ever-present fallback surface, unaffected by a diagram-layout
 * failure.
 */
@Component({
  selector: 'fleet-graph-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GraphDiagramView, GraphNodeTable, GraphSessionTable, KitAsyncState, KitButton],
  template: `
    <section class="gd-panel graph-detail" aria-label="Graph detail" data-testid="graph-detail">
      <fleet-kit-async-state
        [state]="state()"
        loadingText="Loading graph…"
        loadingTestid="graph-detail-loading"
        [errorText]="'Unknown graph: ' + graphId()"
        errorTestid="graph-detail-error"
      >
      @if (graph(); as g) {
        <div class="body" data-testid="graph-detail-body">
          <div class="gd-hdr">
            <span class="gd-lbl">{{ g.name }}</span>
            <span
              class="lifecycle-badge"
              data-testid="graph-detail-lifecycle-badge"
              [class.retired]="g.retired"
              [class.enabled]="!g.retired"
              >{{ g.retired ? 'retired' : 'enabled' }}</span
            >
            <span class="gid" data-testid="graph-detail-graph-id">{{ g.graph_id }}</span>
          </div>
          <!-- Retire/re-enable are graph authoring — gated on graph:edit (admin-tier,
               issue #93): a contributor reads the structure + badge but not the controls. -->
          @if (canEdit()) {
            <div class="lifecycle-actions">
              @if (g.retired) {
                <fleet-kit-button testid="graph-detail-enable" (click)="onEnable(g.graph_id)"
                  >Re-enable</fleet-kit-button
                >
              } @else {
                <fleet-kit-button testid="graph-detail-retire" variant="danger" (click)="onRetire(g.graph_id)"
                  >Retire</fleet-kit-button
                >
              }
            </div>
          }
          @if (actionError(); as err) {
            <p class="lifecycle-error" data-testid="graph-detail-lifecycle-error">{{ err }}</p>
          }
          <p class="entry" data-testid="graph-detail-entry">
            Entry node: <strong>{{ entryNodeName() }}</strong>
          </p>

          <fleet-graph-diagram-view [graph]="g" data-testid="graph-detail-diagram" />

          <fleet-graph-node-table [nodes]="nodes()" [entryNodeId]="g.entry_node_id" />

          <fleet-graph-session-table [sessions]="sessions()" />

          <div class="section" data-testid="graph-detail-edges">
            <span class="gd-lbl">Edges &amp; choices</span>
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
            <span class="gd-lbl">Prompts</span>
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
      </fleet-kit-async-state>
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
    .gd-lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .gd-panel {
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border: 1px solid var(--bezel);
      min-height: 0;
    }
    .gd-hdr {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      background: var(--overlay-25);
    }
    .gid {
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .lifecycle-badge {
      padding: 1px 6px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: var(--fs-xs);
    }
    .lifecycle-badge.enabled {
      color: var(--cyan);
      border: 1px solid var(--cyan);
    }
    .lifecycle-badge.retired {
      color: var(--red);
      border: 1px solid var(--red);
    }
    .lifecycle-actions {
      display: flex;
      padding: 4px 8px 0;
    }
    .lifecycle-error {
      margin: 0;
      padding: 0 8px;
      color: var(--red);
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
  private readonly lifecycleMutation = injectGraphLifecycleMutation();
  private readonly meQuery = injectMeQuery();

  protected readonly graph = computed(() => this.graphQuery.data());

  /** This detail's async state — a single-resource read never reaches `'empty'`
   * (a graph either resolves or the read errors), so `isEmpty` is always
   * `false`, the same reasoning `admin-page.ts`'s `triadState` documents. */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.graphQuery, false));

  /** Whether the current identity may author graphs (`graph:edit`, admin-tier — issue
   * #93) — gates the retire/re-enable controls; `null`/pending resolves to `false`. */
  protected readonly canEdit = computed(() => hasPermission(this.meQuery.data(), 'graph:edit'));

  /** Set on a failed retire/enable (issue #42's report-don't-swallow pattern);
   * cleared at the start of the next attempt. */
  protected readonly actionError = signal<string | null>(null);

  protected readonly nodes = computed<readonly GraphNodeView[]>(() => this.graph()?.nodes ?? []);

  /** The graph's declared sessions (issue #144) — empty for every graph minted before
   * #144, which is what makes the session table render nothing at all there. */
  protected readonly sessions = computed<readonly GraphSessionView[]>(() => this.graph()?.sessions ?? []);

  protected readonly entryNodeName = computed<string>(() => {
    const g = this.graph();
    if (!g) return '';
    return this.nodes().find((n) => n.node_id === g.entry_node_id)?.name ?? g.entry_node_id;
  });

  /** Confirm, then fire the retire mutation (issue #101) — mirrors
   * `chunk-detail-header.ts`'s confirm-then-emit pattern for pause/detach. */
  protected onRetire(graphId: string): void {
    const confirmed = globalThis.confirm(
      `Retire graph ${graphId}? It is excluded from name resolution and refuses new ` +
        `re-pins; any chunk already running on it is left to run out.`,
    );
    if (!confirmed) return;
    this.actionError.set(null);
    this.lifecycleMutation.mutate(
      { graphId, retired: true },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Retire failed.')) },
    );
  }

  /** Confirm, then fire the enable mutation (issue #101). */
  protected onEnable(graphId: string): void {
    const confirmed = globalThis.confirm(`Re-enable graph ${graphId}? It resumes normal newest-per-name derivation.`);
    if (!confirmed) return;
    this.actionError.set(null);
    this.lifecycleMutation.mutate(
      { graphId, retired: false },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Enable failed.')) },
    );
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
