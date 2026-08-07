import type { GraphNodeView } from '../api/hub';

/** A node's `produces:` names, kind dropped — `produces:` is kind-carrying on the wire
 * (`{name, kind}[]`, issue #143), but both the detail table's Produces column
 * (`graph-detail.ts`) and the diagram's meta line (`graph-layout.ts`) render a
 * name-only summary, matching the pre-#143 display exactly. Shared so the two callers
 * can't drift on how `produces` collapses to a display string. */
export function producesNames(node: GraphNodeView): readonly string[] | undefined {
  return node.produces?.map((p) => p.name);
}

/** A node's `session:` in the form it was **authored** — `resume:<node>` for a targeted
 * resume (issue #115), `fresh:<session>` / `resume:<session>` for a graph-level named
 * session (issue #144), bare `resume`/`fresh` otherwise. The wire splits the authored
 * value in two (`session` carries the mode, `session_source` the reference target, per
 * `SessionRef`), so rendering `session` alone silently drops the targeting and an
 * operator cannot tell a targeted resume from a plain one. Recombining the two covers
 * all five forms with no branching: `session_source` is null exactly for the two bare
 * ones. The target name alone does not say whether it is a declared session or a node —
 * the graph's Sessions table (`graph-session-table.ts`) is what resolves that. Shared so
 * the detail table's Session column (`graph-node-table.ts`) and the diagram's meta line
 * (`graph-layout.ts`) can't drift on how the two fields recombine. */
export function sessionLabel(node: GraphNodeView): string {
  return node.session_source ? `${node.session}:${node.session_source}` : node.session;
}

/** A node's retries in `max → exhausted` form (the `→ exhausted` suffix only when
 * set), or `—` when the node declares no retry limit at all. Shared so the detail
 * table's Retries column (`graph-node-table.ts`) and the diagram's detail pane
 * (`graph-diagram-detail.ts`) can't drift on how the two fields combine. */
export function retriesLabel(node: GraphNodeView): string {
  if (node.retries_max === undefined || node.retries_max === null) return '—';
  const exhausted = node.retries_exhausted ? ` → ${node.retries_exhausted}` : '';
  return `${node.retries_max}${exhausted}`;
}

/** A list column's display form — comma-joined, or `—` when empty/absent.
 * Shared so `checks`/`produces` render identically in the node table and the
 * diagram's detail pane. */
export function listOrDash(values: readonly string[] | undefined): string {
  return values && values.length > 0 ? values.join(', ') : '—';
}
