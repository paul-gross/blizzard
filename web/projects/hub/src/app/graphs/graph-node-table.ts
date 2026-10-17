import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { type GraphNodeView, KitBadge } from 'fleet';
import { listOrDash, producesNames, retriesLabel, sessionLabel } from './graph-node';

/**
 * The graph detail's node table (executor, session, judged-by, retries,
 * checks, produces). Presentational
 * only: `nodes`/`entryNodeId` are plain inputs, no query/mutation injection
 * (`bzh:frontend-container-presentational`).
 *
 * The entry marker is `fleet-kit-badge`'s `soft` pill (`bzh:frontend-kit-floor`),
 * its tone chosen for color.
 */
@Component({
  selector: 'app-graph-node-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  templateUrl: './graph-node-table.html',
  styleUrl: './graph-node-table.css',
})
export class GraphNodeTable {
  readonly nodes = input.required<readonly GraphNodeView[]>();
  readonly entryNodeId = input.required<string>();

  protected readonly retriesLabel = retriesLabel;
  protected readonly listOrDash = listOrDash;
  protected readonly producesNames = producesNames;
  protected readonly sessionLabel = sessionLabel;
}
