export { ChunkGeneralTab } from './chunk-general-tab';
export { ChunkPage } from './chunk-page';
export { ChunkPageRoute } from './chunk-page-route';
export type { ChunkDetail as ChunkDetailAggregate } from './chunk-detail.model';
export { provideChunkPageDaemon, type ChunkPageActions } from './chunk-page-daemon';
export {
  injectChunkDetailQuery,
  injectHubChunkDetailQuery,
  injectHubChunkWorkItemsQuery,
} from './chunk-detail.query';
export { fromWorkItemsView } from './work-items.translate';
export { fromBounceView, fromChunkFlowPlaceView, fromFlowFactView } from './chunk-wait.translate';
