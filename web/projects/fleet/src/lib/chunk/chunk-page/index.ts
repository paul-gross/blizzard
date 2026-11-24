export { ChunkGeneralTab } from './chunk-general-tab';
export { ChunkPage } from './chunk-page';
export { ChunkPageRoute } from './chunk-page-route';
export type { ChunkDetail as ChunkDetailAggregate, ChunkPause, ChunkWorkRef } from './chunk-detail.model';
export { fromWorkItemsView } from './work-items.translate';
export { provideChunkPageDaemon, type ChunkPageActions } from './chunk-page-daemon';
export {
  injectChunkDetailQuery,
  injectHubChunkDetailQuery,
  injectHubChunkWorkItemsQuery,
} from './chunk-detail.query';
export { fromBounceView, fromChunkFlowPlaceView, fromFlowFactView } from './chunk-wait.translate';
