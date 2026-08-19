import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { GraphSessionView } from '../api/hub';

/**
 * The graph detail's **session declaration** table (issue #144) — the graph-level
 * `sessions:` map, read-only: each declaration's prioritized model preference list,
 * effort, compaction window (blizzard#343), and rotation bounds.
 *
 * It is what makes a node meta line reading `fresh:code` legible: `sessionLabel`
 * recombines the wire's `session`/`session_source` pair into the authored form, but the
 * target name alone does not say whether `code` is a declared session or a node, nor what
 * the declaration carries. This table answers both.
 *
 * Presentational only: `sessions` is a plain input, no query/mutation injection
 * (`bzh:frontend-container-presentational`). Renders nothing at all for a graph that
 * declares none, which is every graph minted before #144 — an empty table with a heading
 * would read as a missing-data bug rather than as "this graph declares no sessions".
 */
@Component({
  selector: 'fleet-graph-session-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './graph-session-table.html',
  styleUrl: './graph-session-table.css',
})
export class GraphSessionTable {
  readonly sessions = input.required<readonly GraphSessionView[]>();

  /** The declared rotation bounds as one cell, only the thresholds actually declared —
   * a policy with none, and a declaration with no `rotate:` at all, both read `—`.
   * `max_invocations` counts harness invocations (spawn/resume/judge/nudge), not
   * node-steps, so the label says so rather than leaving an operator to assume. */
  protected rotateLabel(session: GraphSessionView): string {
    const rotate = session.rotate;
    if (!rotate) return '—';
    const parts: string[] = [];
    if (rotate.max_context_tokens != null) parts.push(`${rotate.max_context_tokens} ctx tokens`);
    if (rotate.max_transcript_bytes != null) parts.push(`${rotate.max_transcript_bytes} transcript bytes`);
    if (rotate.max_invocations != null) parts.push(`${rotate.max_invocations} invocations`);
    return parts.length > 0 ? parts.join(', ') : '—';
  }

  protected listOrDash(values: readonly string[] | undefined): string {
    return values && values.length > 0 ? values.join(', ') : '—';
  }
}
