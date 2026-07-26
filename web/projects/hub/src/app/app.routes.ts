import type { Routes } from '@angular/router';
import { matchesMobileViewport } from 'fleet';

/**
 * The hub app's top-level route table. `''` redirects to `/board` (today's default
 * surface). `/graphs` and `/graphs/:graphId` both render `GraphsPage` — the list
 * stays mounted and the optional `graphId` param drives the detail, so selecting a
 * version is a deep-linkable, refresh-safe master/detail rather than a full
 * route swap that would drop the list.
 *
 * `board` is **one URL, two shells**: design review moved the mobile/desktop fork
 * out of `BoardPage` (a per-page `@if` on `ViewportService.mode`) and into the
 * route table, so a page component stays single-shell and the fork exists exactly
 * once. Two entries share the `board` path — the mobile glance shell first, guarded
 * by `matchesMobileViewport` (`fleet`'s `CanMatchFn`), then the unguarded desktop
 * `BoardPage` as the fallback the router falls through to when the guard declines
 * to match. The path itself never forks: `/board` is the one deep link either mode
 * serves (universal deep links are load-bearing), and `provideViewportRenavigation`
 * (wired in `app.config.ts`) re-navigates in place whenever the effective mode
 * flips, so the guard re-evaluates and the shell swaps without a URL change.
 */
export const routes: Routes = [
  { path: '', redirectTo: 'board', pathMatch: 'full' },
  {
    path: 'board',
    canMatch: [matchesMobileViewport],
    loadComponent: () => import('./board/glance/glance-board').then((m) => m.GlanceBoard),
  },
  { path: 'board', loadComponent: () => import('./board/board-page').then((m) => m.BoardPage) },
  // The mobile drill-down: a chunk's detail, and one level deeper, a single
  // artifact. Deliberately **unguarded** — desktop reads a chunk in the board's
  // own dock and never links here, but a shared URL opened on a laptop still
  // resolves to a usable page rather than a no-route-matched dead end.
  { path: 'board/chunk/:chunkId', loadComponent: () => import('./board/chunk/chunk-page').then((m) => m.ChunkPage) },
  {
    path: 'board/chunk/:chunkId/artifact/:artifactKey',
    loadComponent: () => import('./board/chunk/artifact-page').then((m) => m.ArtifactPage),
  },
  { path: 'graphs', loadComponent: () => import('./graphs/graphs-page').then((m) => m.GraphsPage) },
  { path: 'graphs/:graphId', loadComponent: () => import('./graphs/graphs-page').then((m) => m.GraphsPage) },
  { path: 'events', loadComponent: () => import('./events/events-page').then((m) => m.EventsPage) },
  // The login surface (issue #93) — public, reached directly or via the 401
  // interceptor. Rendered outside the app shell (`App`'s own `authState` branch),
  // so it carries no header/nav chrome of its own.
  { path: 'login', loadComponent: () => import('./login/login-page').then((m) => m.LoginPage) },
  // A deliberate stub (issue #93's scope note) — the admin page itself is #94's; this
  // phase only needs a route the gated nav entry can point at.
  { path: 'admin', loadComponent: () => import('./admin/admin-page').then((m) => m.AdminPage) },
];
