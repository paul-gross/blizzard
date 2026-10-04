import type { Routes } from '@angular/router';

/**
 * The runner app's top-level route table. `''` redirects to
 * `/board` — the panel's existing default surface, selection riding in the
 * URL's query params via the shared `injectChunkUrlSelection`.
 * `/events` is the local fact log at full width. `/board/chunk/:chunkId` is the shared
 * chunk detail page (`fleet`'s `ChunkPage`) the hub mounts on the same path, served from
 * this runner's own daemon. Every route is
 * lazy — mirrors the hub's own `app.routes.ts` — so no page's bundle loads
 * until its tab is actually reached.
 */
export const routes: Routes = [
  { path: '', redirectTo: 'board', pathMatch: 'full' },
  { path: 'board', loadComponent: () => import('../board/board-page').then((m) => m.BoardPage) },
  {
    path: 'board/chunk/:chunkId',
    loadChildren: () => import('../board/chunk/chunk-page.routes').then((m) => m.CHUNK_PAGE_ROUTES),
  },
  { path: 'events', loadComponent: () => import('../events/events-page').then((m) => m.EventsPage) },
];
