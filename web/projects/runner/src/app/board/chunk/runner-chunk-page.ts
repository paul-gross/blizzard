import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { CHUNK_STEP_SESSIONS, ChunkPage, KitAsyncState, provideChunkPageDaemon } from 'fleet';

import { ChunkOwner } from './chunk-owner';

/**
 * The `board/chunk/:chunkId` route — the shared chunk page, mounted once the chunk's runner
 * is known ({@link ChunkOwner}) with that runner's daemon: the runner client addressed under
 * its id, the board as the link base, and no operator-action port, so the page renders
 * read-only, its Node history rows enriched with the runner's own record of each step's
 * session ({@link ChunkOwner.stepSessions}). The runner serves no `/api/me`, so there is no permission model to gate the
 * Transcripts tab on; its own transcript routes are local reads. A chunk no serving runner
 * holds a lease for renders a not-found state instead.
 */
@Component({
  selector: 'app-runner-chunk-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkPage, KitAsyncState],
  providers: [
    ChunkOwner,
    provideChunkPageDaemon(() => {
      const owner = inject(ChunkOwner);
      return { target: () => owner.target(), boardLinkBase: () => ['/board'] };
    }),
    { provide: CHUNK_STEP_SESSIONS, useFactory: () => inject(ChunkOwner).stepSessions },
  ],
  templateUrl: './runner-chunk-page.html',
  styleUrl: './runner-chunk-page.css',
})
export class RunnerChunkPage {
  protected readonly owner = inject(ChunkOwner);
}
