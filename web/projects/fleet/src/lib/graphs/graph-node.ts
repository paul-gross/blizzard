import type { GraphNodeView } from '../api/hub';

/** A node's `produces:` names, kind dropped — `produces:` is kind-carrying on the wire
 * (`{name, kind}[]`, issue #143), but both the detail table's Produces column
 * (`graph-detail.ts`) and the diagram's meta line (`graph-layout.ts`) render a
 * name-only summary, matching the pre-#143 display exactly. Shared so the two callers
 * can't drift on how `produces` collapses to a display string. */
export function producesNames(node: GraphNodeView): readonly string[] | undefined {
  return node.produces?.map((p) => p.name);
}
