import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import type { GraphNodeView, GraphSessionView } from '../api/hub';
import { hasPermission, injectMeQuery } from '../auth/me.query';
import { errorMessage } from '../error-message';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitPanel, KitPanelHeader } from '../kit/kit-panel';
import { asyncState } from '../query-state';
import { GraphDetailHeader } from './graph-detail-header';
import { GraphDetailLifecycle } from './graph-detail-lifecycle';
import { GraphDiagramView } from './graph-diagram-view';
import { injectGraphLifecycleMutation } from './graph-lifecycle.mutations';
import { GraphNodeTable } from './graph-node-table';
import { GraphSessionTable } from './graph-session-table';
import { injectHubGraphQuery } from './graphs.query';

/**
 * The graph explorer's **detail** view — one minted graph's immutable structure,
 * rendered in full: the entry node, a node table (executor, session, judged-by,
 * retries, mode, checks, produces), and the graph-level session declarations (issue
 * #144). Consumes
 * `injectHubGraphQuery` reactively over the `graphId` input, which the host page
 * binds to the `/graphs/:graphId` route param — refresh-safe and deep-linkable by
 * construction (`bzh:generated-client`; no hand-written fetch).
 *
 * Mounts `<fleet-graph-diagram-view>` above the node table — the selectable DAG
 * render of the same `GraphView` plus its detail pane (blizzard#159), no re-fetch;
 * the table stays the ever-present fallback surface, unaffected by a diagram-layout
 * failure. Every edge — its choice, its target, its description, and its prompt
 * addendum — is read by selecting it in that pane, which is the single place the
 * graph's prose is rendered; there is no standing edges-and-choices list repeating
 * it below the tables.
 *
 * Container only: keeps the injections (`injectHubGraphQuery`,
 * `injectGraphLifecycleMutation`, `injectMeQuery`) and the derived state, and
 * composes `KitPanel` (`bzh:frontend-kit-floor`) around five presentational
 * children — {@link GraphDetailHeader} (the panel's own header supplement,
 * which also carries the retire/re-enable control — right-aligned against
 * its own lifecycle text on the header bar), {@link GraphDetailLifecycle}
 * (the action-error line and entry-node line, below the header bar),
 * `fleet-graph-diagram-view`, `fleet-graph-node-table`, and
 * `fleet-graph-session-table` — each of which forwards data down and re-emits
 * outputs up (`bzh:frontend-container-presentational`).
 */
@Component({
  selector: 'fleet-graph-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    GraphDetailHeader,
    GraphDetailLifecycle,
    GraphDiagramView,
    GraphNodeTable,
    GraphSessionTable,
    KitAsyncState,
    KitPanel,
    KitPanelHeader,
  ],
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
   * #93) — gates the retire/re-enable control, forwarded to {@link GraphDetailHeader};
   * `null`/pending resolves to `false`. */
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

  /** Fires the retire mutation once {@link GraphDetailHeader} has already confirmed. */
  protected onRetire(graphId: string): void {
    this.actionError.set(null);
    this.lifecycleMutation.mutate(
      { graphId, retired: true },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Retire failed.')) },
    );
  }

  /** Fires the enable mutation once {@link GraphDetailHeader} has already confirmed. */
  protected onEnable(graphId: string): void {
    this.actionError.set(null);
    this.lifecycleMutation.mutate(
      { graphId, retired: false },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Enable failed.')) },
    );
  }
}
