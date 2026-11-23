import type { GraphNode } from './graph.domain';

/** A node's `session:` in the form it was **authored** — `resume:<node>` for a targeted
 * resume, `fresh:<session>` / `resume:<session>` for a graph-level named
 * session, bare `resume`/`fresh` otherwise. The wire splits the authored
 * value in two (`session` carries the mode, `sessionSource` the reference target, per
 * `SessionRef`), so rendering `session` alone silently drops the targeting and an
 * operator cannot tell a targeted resume from a plain one. Recombining the two covers
 * all five forms with no branching: `sessionSource` is null exactly for the two bare
 * ones. The target name alone does not say whether it is a declared session or a node —
 * the graph's Sessions table (`graph-session-table.ts`) is what resolves that. Shared so
 * the detail table's Session column (`graph-node-table.ts`) and the diagram's meta line
 * (`graph-layout.ts`) can't drift on how the two fields recombine. */
export function sessionLabel(node: GraphNode): string {
  return node.sessionSource ? `${node.session}:${node.sessionSource}` : node.session;
}

/** A node's retries in `max → exhausted` form (the `→ exhausted` suffix only when
 * set), or `—` when the node declares no retry limit at all. Shared so the detail
 * table's Retries column (`graph-node-table.ts`) and the diagram's detail pane
 * (`graph-diagram-detail.ts`) can't drift on how the two fields combine. */
export function retriesLabel(node: GraphNode): string {
  if (node.retriesMax === null) return '—';
  const exhausted = node.retriesExhausted ? ` → ${node.retriesExhausted}` : '';
  return `${node.retriesMax}${exhausted}`;
}

/** A hub node's `with:` arguments in `name: value, value` form, one argument per `;`, or `—`
 * when it passes none. */
export function actionArgsLabel(node: GraphNode): string {
  const args = Object.entries(node.actionArgs);
  return args.length > 0 ? args.map(([name, values]) => `${name}: ${values.join(', ')}`).join('; ') : '—';
}

/** A list column's display form — comma-joined, or `—` when empty/absent.
 * Shared so `checks`/`produces` render identically in the node table and the
 * diagram's detail pane. */
export function listOrDash(values: readonly string[] | undefined): string {
  return values && values.length > 0 ? values.join(', ') : '—';
}
