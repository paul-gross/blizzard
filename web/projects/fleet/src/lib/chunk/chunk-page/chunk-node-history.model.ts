import type { ArtifactView } from '../../api/hub';
import { parseNodeStepKey } from '../../core/node-step';
import { filterArtifactsByStep } from '../chunk-detail/filter-artifacts-by-step';
import { sortArtifacts } from '../chunk-detail/sort-artifacts';

/** A node-step selection key parsed into its `(nodeId, epoch)` pair — `null` when
 * nothing is selected or the key is not a node-step key. */
export function parseSelectedKey(key: string | null): { nodeId: string; epoch: number } | null {
  return key === null ? null : parseNodeStepKey(key);
}

/** The selected node-step's own artifacts, oldest first — none when no step is
 * selected. */
export function stepArtifacts(
  artifacts: readonly ArtifactView[],
  selection: { readonly nodeId: string; readonly epoch: number } | null,
): ArtifactView[] {
  if (selection === null) return [];
  return sortArtifacts(filterArtifactsByStep(artifacts, selection.nodeId, selection.epoch));
}
