import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { GraphNodeView } from '../api/hub';
import { KitBadge } from '../kit/kit-badge';
import { listOrDash, producesNames, retriesLabel, sessionLabel } from './graph-node';

/**
 * The graph detail's node table (executor, session, judged-by, retries, mode,
 * checks, produces) — split out of `graph-detail.ts` (issue #143) so the
 * container stays under the `web:lint` line cap. Presentational
 * only: `nodes`/`entryNodeId` are plain inputs, no query/mutation injection
 * (`bzh:frontend-container-presentational`).
 *
 * The entry marker is `fleet-kit-badge`'s `soft` pill (`bzh:frontend-kit-floor`),
 * the same variant the graph explorer's effective/superseded pills use, so the two
 * graph surfaces share one pill vocabulary. Its tone is chosen for the color the
 * marker already carried — `waiting` is the ladder's `--amber-hi`, the badge's own
 * previous border and text color — not for `Tone`'s documented meaning, exactly as
 * `graph-explorer-list.ts`'s `LIFECYCLE_TONE` picks its three.
 */
@Component({
  selector: 'fleet-graph-node-table',
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
