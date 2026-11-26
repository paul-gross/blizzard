/* The `fleet/chunk-page` entry point: the chunk page's data and translators. */

export type {
  ChunkDetail as ChunkDetailAggregate,
  ChunkPause,
  ChunkWorkRef,
} from '../lib/chunk/chunk-page/chunk-detail.model';
export {
  injectChunkDetailQuery,
  injectHubChunkDetailQuery,
  injectHubChunkWorkItemsQuery,
} from '../lib/chunk/chunk-page/chunk-detail.query';
export { type ChunkPageActions, provideChunkPageDaemon } from '../lib/chunk/chunk-page/chunk-page-daemon';
export {
  fromBounceView,
  fromChunkFlowPlaceView,
  fromFlowFactView,
  fromFlowState,
} from '../lib/chunk/chunk-page/chunk-wait.translate';
export { fromWorkItemsView } from '../lib/chunk/chunk-page/work-items.translate';
