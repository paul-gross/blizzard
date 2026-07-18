import type { Routes } from '@angular/router';

/**
 * The hub app's top-level route table. `''` redirects to `/board` (today's default
 * surface). `/graphs` and `/graphs/:graphId` both render `GraphsPage` — the list
 * stays mounted and the optional `graphId` param drives the detail, so selecting a
 * version is a deep-linkable, refresh-safe master/detail rather than a full
 * route swap that would drop the list.
 */
export const routes: Routes = [
  { path: '', redirectTo: 'board', pathMatch: 'full' },
  { path: 'board', loadComponent: () => import('./board/board-page').then((m) => m.BoardPage) },
  { path: 'graphs', loadComponent: () => import('./graphs/graphs-page').then((m) => m.GraphsPage) },
  { path: 'graphs/:graphId', loadComponent: () => import('./graphs/graphs-page').then((m) => m.GraphsPage) },
];
