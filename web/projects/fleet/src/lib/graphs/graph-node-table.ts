import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { GraphNodeView } from '../api/hub';
import { listOrDash, producesNames, retriesLabel, sessionLabel } from './graph-node';

/**
 * The graph detail's node table (executor, session, judged-by, retries, mode,
 * checks, produces) — split out of `graph-detail.ts` (issue #143) so the
 * container stays under the `web:lint` line cap. Presentational
 * only: `nodes`/`entryNodeId` are plain inputs, no query/mutation injection
 * (`bzh:frontend-container-presentational`).
 */
@Component({
  selector: 'fleet-graph-node-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
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
