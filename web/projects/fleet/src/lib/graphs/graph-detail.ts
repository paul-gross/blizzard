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
 * and every edge with the choice it fires on and its prompt addendum. Consumes
 * `injectHubGraphQuery` reactively over the `graphId` input, which the host page
 * binds to the `/graphs/:graphId` route param — refresh-safe and deep-linkable by
 * construction (`bzh:generated-client`; no hand-written fetch).
 *
 * Mounts `<fleet-graph-diagram-view>` above the node table — the selectable DAG
 * render of the same `GraphView` plus its detail pane (blizzard#159), no re-fetch;
 * the table stays the ever-present fallback surface, unaffected by a diagram-layout
 * failure. A node's prompt/judgement text and an edge's prompt addendum are read via
 * that diagram selection pane, not duplicated here in a standalone prompts list
 * (issue #208).
 */
@Component({
  selector: 'fleet-graph-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GraphDiagramView, GraphNodeTable, GraphSessionTable, KitAsyncState, KitButton],
  templateUrl: './graph-detail.html',
  styleUrl: './graph-detail.css',
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
