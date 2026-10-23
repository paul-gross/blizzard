import type { Routes } from '@angular/router';
import { matchesMobileViewport } from 'fleet/shell';
import { enterInvitedTenant } from '../core/auth/invite-accepted';
import { LENSED_ROUTE_PATH } from '../core/project-lens';
import { projectLensGuard } from './project-lens.guard';

/**
 * The board's views, mounted twice by {@link routes}: at the root, the lens on All, and under `p/:projectSlug`, the
 * lens on one project — so every view and every deep link is reachable with and without a lens. Every path here is
 * relative to its mount, so a redirect (`''` → `board`, a lens's own views) stays under the lens it was hit in.
 *
 * The view table. `''` redirects to `/board` (today's default
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
export const VIEW_ROUTES: Routes = [
  { path: '', redirectTo: 'board', pathMatch: 'full' },
  {
    path: 'board',
    canMatch: [matchesMobileViewport],
    loadComponent: () => import('../board/glance/glance-board').then((m) => m.GlanceBoard),
  },
  { path: 'board', loadComponent: () => import('../board/board-page').then((m) => m.BoardPage) },
  // The mobile drill-down: a chunk's detail, and one level deeper, a single
  // artifact. Deliberately **unguarded** — desktop reads a chunk in the board's
  // own dock and never links here, but a shared URL opened on a laptop still
  // resolves to a usable page rather than a no-route-matched dead end.
  {
    path: 'board/chunk/:chunkId/artifact/:artifactKey',
    loadComponent: () => import('../board/chunk/artifact-page').then((m) => m.ArtifactPage),
  },
  // The shared chunk page, with the hub's daemon provided on its own child route.
  {
    path: 'board/chunk/:chunkId',
    loadChildren: () => import('../board/chunk/chunk-page.routes').then((m) => m.CHUNK_PAGE_ROUTES),
  },
  { path: 'graphs', loadComponent: () => import('../graphs/graphs-page').then((m) => m.GraphsPage) },
  { path: 'graphs/:graphId', loadComponent: () => import('../graphs/graphs-page').then((m) => m.GraphsPage) },
  { path: 'events', loadComponent: () => import('../events/events-page').then((m) => m.EventsPage) },
  // The Fleet tab is **one URL, two shells**, like `board`: the phone's `RunnersPage` first, guarded by
  // `matchesMobileViewport`, then the desktop `FleetPage` the router falls through to. Each carries the `:id`
  // child that selects within it — a host or runner id the desktop list keeps mounted across, and which a phone
  // opening a shared `/fleet/<id>` link still resolves to `RunnersPage` rather than the desktop shell.
  {
    path: 'fleet',
    canMatch: [matchesMobileViewport],
    loadComponent: () => import('../runners/runners-page').then((m) => m.RunnersPage),
    children: [{ path: ':id', children: [] }],
  },
  {
    path: 'fleet',
    loadComponent: () => import('../runners/fleet-page').then((m) => m.FleetPage),
    children: [{ path: ':id', children: [] }],
  },
  // The gardening tab and the admin surface — each subtree is its folder's own route file.
  { path: 'gardening', loadChildren: () => import('../garden/gardening.routes').then((m) => m.GARDENING_ROUTES) },
  { path: 'admin', loadChildren: () => import('../admin/admin.routes').then((m) => m.ADMIN_ROUTES) },
];

/**
 * The hub app's top-level route table: the views at the root (All), the views again under the project lens's segment,
 * whose guard settles the segment on the project's current slug, and the public pages, which carry no lens.
 */
export const routes: Routes = [
  { path: LENSED_ROUTE_PATH, canActivate: [projectLensGuard], children: VIEW_ROUTES },
  // The login surface — public, reached directly or via the 401
  // interceptor. Rendered outside the app shell (`App`'s own `authState` branch),
  // so it carries no header/nav chrome of its own.
  { path: 'login', loadComponent: () => import('../login/login-page').then((m) => m.LoginPage) },
  // An invitation's landing — public like `/login`, and rendered outside the shell
  // whatever the session. The hub lands an accepted invitation on `invite/accepted`,
  // which enters the invited tenant and replaces itself with the board's root; it
  // is listed first, so it is never read as a token.
  { path: 'invite/accepted', canActivate: [enterInvitedTenant], children: [] },
  { path: 'invite/:token', loadComponent: () => import('../login/invite-page').then((m) => m.InvitePage) },
  ...VIEW_ROUTES,
];
