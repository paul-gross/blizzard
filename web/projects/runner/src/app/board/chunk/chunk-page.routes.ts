import type { Routes } from '@angular/router';
import { ChunkPage, provideChunkPageDaemon, runnerClient } from 'fleet';

/** The runner's `board/chunk/:chunkId` child table — the shared chunk page with this
 * runner's daemon provided on the route: its own client and plane, and no operator-action
 * port, so the page renders read-only. The runner serves no `/api/me`, so there is no
 * permission model to gate the Transcripts tab on; its own transcript routes are local
 * reads. */
export const CHUNK_PAGE_ROUTES: Routes = [
  {
    path: '',
    component: ChunkPage,
    providers: [provideChunkPageDaemon({ client: runnerClient, plane: 'runner' })],
  },
];
