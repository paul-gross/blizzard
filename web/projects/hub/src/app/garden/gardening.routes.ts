import type { Routes } from '@angular/router';

import { gardenProjectGuard, gardeningDoorwayGuard } from './garden-lens.guard';

/**
 * The gardening tab — a top-level peer of board/graphs/events, not a
 * panel inside any of them. Five deep-linkable children, one per noun the garden
 * machinery itself has: scopes, routines, runs, findings, proposals — every one of
 * them unrelated to its neighbors, so every one of them gets its own tab and its
 * own list.
 *
 * All five have one shape: the tab's list *is* the route, and its detail pane is a
 * child route under it — a bare child that selects nothing, and a `:param` child
 * naming the selection, both mounting the same detail component. Angular reuses a
 * route's component only across the same route config, so two flat routes onto one
 * component would tear the whole tab down on every row click. Nested, only the
 * right-hand child is swapped; the list, its filters, and its scroll position all
 * survive. Filter state itself rides the query string (`route-state.ts`), which
 * makes a filtered view shareable by URL as well as durable.
 *
 * Every noun belongs to one project, so the tabs are a project's garden and exist only under its lens
 * (`/p/{project}/gardening/…`). On All the bare path is the doorway — one card per project and no tabs — and a tab's
 * link on All is followed into a project by {@link gardenProjectGuard}.
 */
export const GARDENING_ROUTES: Routes = [
  {
    path: '',
    pathMatch: 'full',
    canActivate: [gardeningDoorwayGuard],
    loadComponent: () => import('./doorway/gardening-doorway').then((m) => m.GardeningDoorway),
  },
  {
    path: '',
    canActivate: [gardenProjectGuard],
    loadComponent: () => import('./gardening-page').then((m) => m.GardeningPage),
    children: [
      // A scope has no id of its own — its slug *is* the id.
      {
        path: 'scopes',
        loadComponent: () => import('./scopes/gardening-scopes-page').then((m) => m.GardeningScopesPage),
        children: [
          {
            path: '',
            loadComponent: () => import('./scopes/gardening-scope-detail').then((m) => m.GardeningScopeDetail),
          },
          {
            path: ':scopeSlug',
            loadComponent: () => import('./scopes/gardening-scope-detail').then((m) => m.GardeningScopeDetail),
          },
        ],
      },
      // Routines are keyed by `name` (unique per routine), not id.
      {
        path: 'routines',
        loadComponent: () => import('./routines/gardening-routines-page').then((m) => m.GardeningRoutinesPage),
        children: [
          {
            path: '',
            loadComponent: () => import('./routines/gardening-routine-detail').then((m) => m.GardeningRoutineDetail),
          },
          {
            path: ':routineName',
            loadComponent: () => import('./routines/gardening-routine-detail').then((m) => m.GardeningRoutineDetail),
          },
        ],
      },
      {
        path: 'runs',
        loadComponent: () => import('./runs/gardening-runs-page').then((m) => m.GardeningRunsPage),
        children: [
          {
            path: '',
            loadComponent: () => import('./runs/gardening-run-detail').then((m) => m.GardeningRunDetail),
          },
          {
            path: ':chunkId',
            loadComponent: () => import('./runs/gardening-run-detail').then((m) => m.GardeningRunDetail),
          },
        ],
      },
      // The tab the nesting above earns its keep on: this list holds four filters,
      // which the flat pair silently reset on every row click.
      {
        path: 'findings',
        loadComponent: () => import('./findings/gardening-findings-page').then((m) => m.GardeningFindingsPage),
        children: [
          {
            path: '',
            loadComponent: () => import('./findings/gardening-finding-detail').then((m) => m.GardeningFindingDetail),
          },
          {
            path: ':findingId',
            loadComponent: () => import('./findings/gardening-finding-detail').then((m) => m.GardeningFindingDetail),
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
        loadComponent: () => import('./proposals/gardening-proposals-page').then((m) => m.GardeningProposalsPage),
        children: [
          {
            path: '',
            loadComponent: () =>
              import('./proposals/gardening-proposal-detail').then((m) => m.GardeningProposalDetail),
          },
          {
            path: ':proposalId',
            loadComponent: () =>
              import('./proposals/gardening-proposal-detail').then((m) => m.GardeningProposalDetail),
          },
        ],
      },
    ],
  },
];
