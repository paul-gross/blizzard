import type { Routes } from '@angular/router';
import { ChunkPage, provideChunkPageDaemon } from 'fleet';

import { injectActiveRunner, injectActiveRunnerTarget } from '../../core/active-runner';

/** The runner's `board/chunk/:chunkId` child table — the shared chunk page with the
 * active runner's daemon provided on the route: the runner client addressed under the
 * active runner's id, that runner's board as the link base, and no operator-action port,
 * so the page renders read-only. The runner serves no `/api/me`, so there is no
 * permission model to gate the Transcripts tab on; its own transcript routes are local
 * reads. */
export const CHUNK_PAGE_ROUTES: Routes = [
  {
    path: '',
    component: ChunkPage,
    providers: [
      provideChunkPageDaemon(() => {
        const runnerId = injectActiveRunner();
        const target = injectActiveRunnerTarget();
        return {
          target: () => target(),
          boardLinkBase: () => ['/runners', runnerId() ?? '', 'board'],
        };
      }),
    ],
  },
];
