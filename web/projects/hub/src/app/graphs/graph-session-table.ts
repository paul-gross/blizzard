import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { harnessName } from 'fleet';
import type { GraphSession } from './graph.domain';

/**
 * The graph detail's **session declaration** table — the graph-level
 * `sessions:` map, read-only: each declaration's prioritized model preference list,
 * acceptable harness set, effort, compaction window, and
 * rotation bounds.
 *
 * It is what makes a node meta line reading `fresh:code` legible: `sessionLabel`
 * recombines the node's `session`/`sessionSource` pair into the authored form, but the
 * target name alone does not say whether `code` is a declared session or a node, nor what
 * the declaration carries. This table answers both.
 *
 * Presentational only: `sessions` is a plain input, no query/mutation injection
 * (`bzh:frontend-container-presentational`). Renders nothing at all for a graph that
 * declares none, which is every graph minted before session declarations — an empty table with a heading
 * would read as a missing-data bug rather than as "this graph declares no sessions".
 */
@Component({
  selector: 'app-graph-session-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './graph-session-table.html',
  styleUrl: './graph-session-table.css',
})
export class GraphSessionTable {
  readonly sessions = input.required<readonly GraphSession[]>();

  /** The declared rotation bounds as one cell, only the thresholds actually declared —
   * a policy with none, and a declaration with no `rotate:` at all, both read `—`.
   * `maxInvocations` counts harness invocations (spawn/resume/judge/nudge), not
   * node-steps, so the label says so rather than leaving an operator to assume. */
  protected rotateLabel(session: GraphSession): string {
    const rotate = session.rotate;
    if (!rotate) return '—';
    const parts: string[] = [];
    if (rotate.maxContextTokens != null) parts.push(`${rotate.maxContextTokens} ctx tokens`);
    if (rotate.maxTranscriptBytes != null) parts.push(`${rotate.maxTranscriptBytes} transcript bytes`);
    if (rotate.maxInvocations != null) parts.push(`${rotate.maxInvocations} invocations`);
    return parts.length > 0 ? parts.join(', ') : '—';
  }

  protected listOrDash(values: readonly string[] | undefined): string {
    return values && values.length > 0 ? values.join(', ') : '—';
  }

  protected harnesses(session: GraphSession): string {
    return session.harnesses?.length ? session.harnesses.map(harnessName).join(', ') : '—';
  }
}
