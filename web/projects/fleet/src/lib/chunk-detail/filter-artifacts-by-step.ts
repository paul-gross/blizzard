import type { ArtifactView } from '../api/hub';

/**
 * The chunk artifact store, narrowed to exactly one node-step's own entries — an
 * `ArtifactView`'s `(node_id, epoch)` matched by exact equality, never latest-by-node:
 * artifacts are append-only with latest-by-epoch resolution left to the reader
 * ({@link ArtifactView}'s own doc comment), so a node re-run under a later epoch must not
 * surface the earlier epoch's artifacts under the later step.
 *
 * The single owner of that filter (`canon:one-owner`), beside {@link sortArtifacts} —
 * the Node history tab's per-step artifact panel is its one caller today.
 */
export function filterArtifactsByStep(
  artifacts: readonly ArtifactView[],
  nodeId: string,
  epoch: number,
): ArtifactView[] {
  return artifacts.filter((a) => a.node_id === nodeId && a.epoch === epoch);
}
