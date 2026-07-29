import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { GraphNodeView } from '../api/hub';
import { listOrDash, producesNames, retriesLabel, sessionLabel } from './graph-node';

/**
 * The graph detail's node table (executor, session, judged-by, retries, mode,
 * checks, produces) — split out of `graph-detail.ts` (issue #143) so the
 * container stays under the `web:structural-gate` line cap. Presentational
 * only: `nodes`/`entryNodeId` are plain inputs, no query/mutation injection
 * (`bzh:frontend-container-presentational`).
 */
@Component({
  selector: 'fleet-graph-node-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
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
              @if (node.node_id === entryNodeId()) {
                <span class="entry-badge" data-testid="graph-detail-entry-badge">entry</span>
              }
            </td>
            <td>{{ node.executor }}</td>
            <td>{{ sessionLabel(node) }}</td>
            <td>{{ node.judged_by }}</td>
            <td>{{ retriesLabel(node) }}</td>
            <td>{{ node.mode ?? '—' }}</td>
            <td>{{ listOrDash(node.checks) }}</td>
            <td>{{ listOrDash(producesNames(node)) }}</td>
          </tr>
        }
      </tbody>
    </table>
  `,
  styles: `
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
      background: var(--overlay-25);
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
  `,
})
export class GraphNodeTable {
  readonly nodes = input.required<readonly GraphNodeView[]>();
  readonly entryNodeId = input.required<string>();

  protected readonly retriesLabel = retriesLabel;
  protected readonly listOrDash = listOrDash;
  protected readonly producesNames = producesNames;
  protected readonly sessionLabel = sessionLabel;
}
