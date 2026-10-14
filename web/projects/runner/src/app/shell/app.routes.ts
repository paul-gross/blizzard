import type { Routes } from '@angular/router';

/**
 * The runner app's top-level route table — the host's panel. `''` is the host page:
 * the runners the host serves, its environments, and its subscriptions. Each runner's own
 * pages sit under `runners/:runnerId`: `board` (the machine panel, selection riding in
 * the URL's query params via the shared `injectChunkUrlSelection`), `board/chunk/:chunkId`
 * (the shared chunk detail page, `fleet`'s `ChunkPage`, the same page the hub mounts),
 * `events` (the runner's fact log at full width), and `machine` (its pause control, hub
 * link, and harness health). Only the shell reads `:runnerId`, into the active-runner
 * signal (`core/active-runner.ts`). Any other path — an old root `/board` or `/events`
 * included — renders the host page, with no redirect. Every route is lazy — mirrors the
 * hub's own `app.routes.ts` — so no page's bundle loads until it is reached.
 */
export const routes: Routes = [
  { path: '', pathMatch: 'full', loadComponent: () => import('../host/host-page').then((m) => m.HostPage) },
  {
    path: 'runners/:runnerId',
    children: [
      { path: 'board', loadComponent: () => import('../board/board-page').then((m) => m.BoardPage) },
      {
        path: 'board/chunk/:chunkId',
        loadChildren: () => import('../board/chunk/chunk-page.routes').then((m) => m.CHUNK_PAGE_ROUTES),
      },
      { path: 'events', loadComponent: () => import('../events/events-page').then((m) => m.EventsPage) },
      { path: 'machine', loadComponent: () => import('../machine/machine-page').then((m) => m.MachinePage) },
    ],
  },
  { path: '**', loadComponent: () => import('../host/host-page').then((m) => m.HostPage) },
];
