import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitBadge } from 'fleet/kit';
import { listOrDash, retriesLabel, sessionLabel } from './graph-node';
import { type GraphNode, graphVocabularyText } from './graph.domain';

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
  readonly nodes = input.required<readonly GraphNode[]>();
  readonly entryNodeId = input.required<string>();

  protected readonly retriesLabel = retriesLabel;
  protected readonly listOrDash = listOrDash;
  protected readonly vocabularyText = graphVocabularyText;
  protected readonly sessionLabel = sessionLabel;
}
