import type { Routes } from '@angular/router';

import { legacyRunnerRedirect } from './legacy-runner-redirect';

/**
 * The runner app's top-level route table — one fixed set of pages over the whole host:
 * `board` (the panel's board, selection riding in the URL's `?chunk=` via the shared
 * `injectChunkUrlSelection`), `board/chunk/:chunkId` (the shared chunk detail page,
 * `fleet`'s `ChunkPage`, mounted for the runner that owns the chunk), `events` (the fact
 * log), and `machine` (the host and each runner's own machine). Which runner and project
 * they show is the URL's `?runner=` and `?project=`, read by `core/panel-filters.ts` alone.
 *
 * `''` and any unknown path land on the board. A `runners/:runnerId/…` URL from the
 * per-runner layout redirects to its flat page, the runner named in `?runner=`
 * ({@link legacyRunnerRedirect}). Every page is lazy — mirrors the hub's own
 * `app.routes.ts` — so no page's bundle loads until it is reached.
 */
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'board' },
  { path: 'board', loadComponent: () => import('../board/board-page').then((m) => m.BoardPage) },
  {
    path: 'board/chunk/:chunkId',
    loadComponent: () => import('../board/chunk/runner-chunk-page').then((m) => m.RunnerChunkPage),
  },
  { path: 'events', loadComponent: () => import('../events/events-page').then((m) => m.EventsPage) },
  { path: 'machine', loadComponent: () => import('../machine/machine-page').then((m) => m.MachinePage) },
  { path: 'runners/:runnerId', canActivate: [legacyRunnerRedirect], children: [{ path: '**', children: [] }] },
  { path: '**', redirectTo: 'board' },
];
