import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { GraphSummaryView } from '../api/hub';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitPanel } from '../kit/kit-panel';
import { asyncState } from '../query-state';
import { GraphExplorerList } from './graph-explorer-list';
import { injectHubGraphsQuery } from './graphs.query';

/**
 * The graph explorer's **container** — injects `injectHubGraphsQuery` and maps it to
 * a `KitAsyncStateValue`, forwarding the resolved summaries and the `selectedGraphId`
 * input down to {@link GraphExplorerList}, which owns the grouped-lineage rendering
 * and expansion state (`bzh:frontend-container-presentational`). Follows
 * `runner-panel.ts`: a standalone `fleet-`prefixed, OnPush container over the
 * generated client (bzh:generated-client) via TanStack Query.
 */
@Component({
  selector: 'fleet-graph-explorer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel, GraphExplorerList],
  templateUrl: './graph-explorer.html',
  styleUrl: './graph-explorer.css',
})
export class GraphExplorer {
  protected readonly graphsQuery = injectHubGraphsQuery();

  /** The currently open detail's graph id, or `null` — forwarded to the list to
   * highlight its row. */
  readonly selectedGraphId = input<string | null>(null);

  /** Emitted with the `graph_id` the list resolved a click to — an effective or
   * superseded row, or a group's effective version when its header expands the
   * group. */
  readonly selectGraph = output<string>();

  /** The resolved graph summaries, or `[]` while pending/errored. */
  protected readonly list = computed<readonly GraphSummaryView[]>(() => this.graphsQuery.data() ?? []);

  /** This container's async state. `isEmpty` reads the resolved list's own length:
   * every summary belongs to some group, so the list is empty exactly when the
   * grouping the list derives from it would be too. */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.graphsQuery, this.list().length === 0));
}
