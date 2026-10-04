import type { Routes } from '@angular/router';
import { matchesMobileViewport } from 'fleet/shell';

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
  // The mobile Fleet tab, mobile-only by `canMatch`. A desktop-width hit on
  // this path (a shared link opened on a laptop, or a live viewport flip)
  // redirects to `board` instead of mounting a desktop-only `RunnersPage`.
  {
    path: 'fleet',
    canMatch: [matchesMobileViewport],
    loadComponent: () => import('../runners/runners-page').then((m) => m.RunnersPage),
  },
  { path: 'fleet', redirectTo: 'board' },
  // The gardening tab — a top-level peer of board/graphs/events, not a
  // panel inside any of them. Five deep-linkable children, one per noun the garden
  // machinery itself has: scopes, routines, runs, findings, proposals — every one of
  // them unrelated to its neighbors, so every one of them gets its own tab and its
  // own list; none of the five shares a selection with another any more.
  //
  // All five have one shape: the tab's list *is* the route, and its detail pane is a
  // child route under it — a bare child that selects nothing, and a `:param` child
  // naming the selection, both mounting the same detail component. Angular reuses a
  // route's component only across the same route config, so the flat pair these grew
  // from (two routes onto one component) tore the whole tab down and rebuilt it on
  // every row click, silently discarding the filters the list held. Nested, only the
  // right-hand child is swapped; the list, its filters, and its scroll position all
  // survive. Filter state itself rides the query string (`route-state.ts`), which
  // makes a filtered view shareable by URL as well as durable.
  {
    path: 'gardening',
    loadComponent: () => import('../garden/gardening-page').then((m) => m.GardeningPage),
    children: [
      { path: '', redirectTo: 'scopes', pathMatch: 'full' },
      // A scope has no id of its own — its slug *is* the id.
      {
        path: 'scopes',
        loadComponent: () => import('../garden/scopes/gardening-scopes-page').then((m) => m.GardeningScopesPage),
        children: [
          {
            path: '',
            loadComponent: () => import('../garden/scopes/gardening-scope-detail').then((m) => m.GardeningScopeDetail),
          },
          {
            path: ':scopeSlug',
            loadComponent: () => import('../garden/scopes/gardening-scope-detail').then((m) => m.GardeningScopeDetail),
          },
        ],
      },
      // Routines are keyed by `name` (unique per routine), not id.
      {
        path: 'routines',
        loadComponent: () => import('../garden/routines/gardening-routines-page').then((m) => m.GardeningRoutinesPage),
        children: [
          {
            path: '',
            loadComponent: () => import('../garden/routines/gardening-routine-detail').then((m) => m.GardeningRoutineDetail),
          },
          {
            path: ':routineName',
            loadComponent: () => import('../garden/routines/gardening-routine-detail').then((m) => m.GardeningRoutineDetail),
          },
        ],
      },
      {
        path: 'runs',
        loadComponent: () => import('../garden/runs/gardening-runs-page').then((m) => m.GardeningRunsPage),
        children: [
          {
            path: '',
            loadComponent: () => import('../garden/runs/gardening-run-detail').then((m) => m.GardeningRunDetail),
          },
          {
            path: ':chunkId',
            loadComponent: () => import('../garden/runs/gardening-run-detail').then((m) => m.GardeningRunDetail),
          },
        ],
      },
      // The tab the nesting above earns its keep on: this list holds four filters,
      // which the flat pair silently reset on every row click.
      {
        path: 'findings',
        loadComponent: () => import('../garden/findings/gardening-findings-page').then((m) => m.GardeningFindingsPage),
        children: [
          {
            path: '',
            loadComponent: () => import('../garden/findings/gardening-finding-detail').then((m) => m.GardeningFindingDetail),
          },
          {
            path: ':findingId',
            loadComponent: () => import('../garden/findings/gardening-finding-detail').then((m) => m.GardeningFindingDetail),
          },
        ],
      },
      // A proposal is keyed by its own id (`gprop_…`, rendered compactly as `GP-…`).
      // The one place this tab diverges from its four siblings on desktop: on a
      // docket with anything in it the list route sends the bare path to the first
      // row of the filtered set. Mobile leaves the bare path on the list so it can
      // serve as the drill-down's first screen.
      {
        path: 'proposals',
        loadComponent: () => import('../garden/proposals/gardening-proposals-page').then((m) => m.GardeningProposalsPage),
        children: [
          {
            path: '',
            loadComponent: () =>
              import('../garden/proposals/gardening-proposal-detail').then((m) => m.GardeningProposalDetail),
          },
          {
            path: ':proposalId',
            loadComponent: () =>
              import('../garden/proposals/gardening-proposal-detail').then((m) => m.GardeningProposalDetail),
          },
        ],
      },
    ],
  },
  // The login surface — public, reached directly or via the 401
  // interceptor. Rendered outside the app shell (`App`'s own `authState` branch),
  // so it carries no header/nav chrome of its own.
  { path: 'login', loadComponent: () => import('../login/login-page').then((m) => m.LoginPage) },
  // The admin surface — the route the permission-gated nav entry points at.
  { path: 'admin', loadComponent: () => import('../admin/admin-page').then((m) => m.AdminPage) },
];
