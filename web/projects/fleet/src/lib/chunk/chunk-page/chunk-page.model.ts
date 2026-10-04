import type { ChunkNeighborView } from '../../api/hub';
import type { KitAsyncStateValue } from '../../kit/kit-async-state';
import type { KitTabOption } from '../../kit/kit-tabs';

const BASE_TAB_OPTIONS: readonly KitTabOption[] = [
  { value: 'general', label: 'General', testid: 'tab-general' },
  { value: 'node-history', label: 'Node history', testid: 'tab-node-history' },
  { value: 'artifacts', label: 'Artifacts', testid: 'tab-artifacts' },
];

const TRANSCRIPTS_TAB_OPTION: KitTabOption = { value: 'transcripts', label: 'Transcripts', testid: 'tab-transcripts' };

/** The chunk page's tab strip — the Transcripts option shown only when the identity
 * may read transcripts. */
export function chunkTabOptions(canReadTranscripts: boolean): readonly KitTabOption[] {
  return canReadTranscripts ? [...BASE_TAB_OPTIONS, TRANSCRIPTS_TAB_OPTION] : BASE_TAB_OPTIONS;
}

/** Which pre-detail state renders — a failed read is not the same as a slow one. */
export function preDetailState(isError: boolean): KitAsyncStateValue {
  return isError ? 'error' : 'loading';
}

/** The chunk ids of every edge not yet satisfied — a satisfied edge blocks nothing
 * and is left off. */
export function openEdgeIds(edges: readonly ChunkNeighborView[]): readonly string[] {
  return edges.filter((n) => !n.satisfied).map((n) => n.chunk_id);
}
