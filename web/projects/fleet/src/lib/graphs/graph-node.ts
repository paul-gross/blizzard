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
 * resume (issue #115), bare `resume`/`fresh` otherwise. The wire splits the authored
 * value in two (`session` carries the mode, `session_source` the target node name, per
 * `classify_session`), so rendering `session` alone silently drops the targeting and an
 * operator cannot tell a targeted resume from a plain one. Shared so the detail table's
 * Session column (`graph-node-table.ts`) and the diagram's meta line (`graph-layout.ts`)
 * can't drift on how the two fields recombine. */
export function sessionLabel(node: GraphNodeView): string {
  return node.session_source ? `${node.session}:${node.session_source}` : node.session;
}
