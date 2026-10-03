import type { Routes } from '@angular/router';
import { ChunkPage } from 'fleet';

import { HUB_CHUNK_PAGE_PROVIDERS } from './hub-chunk-actions';

/** The hub's `board/chunk/:chunkId` child table — the shared chunk page with the hub's
 * daemon provided on the route, loaded lazily so neither reaches the initial chunk. */
export const CHUNK_PAGE_ROUTES: Routes = [{ path: '', component: ChunkPage, providers: HUB_CHUNK_PAGE_PROVIDERS }];
