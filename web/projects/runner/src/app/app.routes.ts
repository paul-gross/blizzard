import type { Routes } from '@angular/router';

/**
 * The runner app's top-level route table (issue #313). `''` redirects to
 * `/board` — the panel's existing default surface, selection riding in the
 * URL's query params exactly as before (issue #99, `panel-selection.ts`).
 * `/events` is the local fact log at full width, split out of the panel's own
 * right rail. `/board/chunk/:chunkId` is the runner-local chunk detail page
 * (issue #318) — mirrors the hub's own `board/chunk/:chunkId`
 * (`hub/src/app/app.routes.ts`), the precedent D4 names for keeping the
 * chunk id in the path while `?attempt=` stays a query param. Every route is
 * lazy — mirrors the hub's own `app.routes.ts` — so no page's bundle loads
 * until its tab is actually reached.
 */
export const routes: Routes = [
  { path: '', redirectTo: 'board', pathMatch: 'full' },
  { path: 'board', loadComponent: () => import('./board/board-page').then((m) => m.BoardPage) },
  {
    path: 'board/chunk/:chunkId',
    loadComponent: () => import('./board/chunk/chunk-detail-page').then((m) => m.ChunkDetailPage),
  },
  { path: 'events', loadComponent: () => import('./events/events-page').then((m) => m.EventsPage) },
];
