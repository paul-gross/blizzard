import type { Routes } from '@angular/router';

/**
 * The admin surface — the route the permission-gated nav entry points at. Members,
 * the members of the tenant the board acts in, is the default child, and `users`, its
 * former path, redirects to it so older links still land; beside it sit the configured
 * nouns, each a list route whose detail is a child, the gardening tabs' own nesting,
 * so a pick swaps only the detail and the list's filter survives in the query string.
 */
export const ADMIN_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () => import('./admin-shell').then((m) => m.AdminShell),
    children: [
      { path: '', redirectTo: 'members', pathMatch: 'full' },
      { path: 'members', loadComponent: () => import('./members/members-page').then((m) => m.MembersPage) },
      { path: 'users', redirectTo: 'members', pathMatch: 'full' },
      {
        path: 'projects',
        loadComponent: () => import('./projects/projects-page').then((m) => m.ProjectsPage),
        children: [
          { path: '', loadComponent: () => import('./projects/project-detail').then((m) => m.ProjectDetail) },
          { path: ':key', loadComponent: () => import('./projects/project-detail').then((m) => m.ProjectDetail) },
        ],
      },
      {
        path: 'work-sources',
        loadComponent: () => import('./work-sources/work-sources-page').then((m) => m.WorkSourcesPage),
        children: [
          { path: '', loadComponent: () => import('./work-sources/work-source-detail').then((m) => m.WorkSourceDetail) },
          { path: ':key', loadComponent: () => import('./work-sources/work-source-detail').then((m) => m.WorkSourceDetail) },
        ],
      },
      {
        path: 'repositories',
        loadComponent: () => import('./repositories/repositories-page').then((m) => m.RepositoriesPage),
        children: [
          { path: '', loadComponent: () => import('./repositories/repository-detail').then((m) => m.RepositoryDetail) },
          { path: ':key', loadComponent: () => import('./repositories/repository-detail').then((m) => m.RepositoryDetail) },
        ],
      },
      {
        path: 'secrets',
        loadComponent: () => import('./secrets/secrets-page').then((m) => m.SecretsPage),
        children: [
          { path: '', loadComponent: () => import('./secrets/secret-detail').then((m) => m.SecretDetail) },
          { path: ':key', loadComponent: () => import('./secrets/secret-detail').then((m) => m.SecretDetail) },
        ],
      },
      {
        path: 'changes',
        loadComponent: () => import('./changes/changes-page').then((m) => m.ChangesPage),
        children: [
          { path: '', loadComponent: () => import('./changes/change-detail').then((m) => m.ChangeDetail) },
          { path: ':key', loadComponent: () => import('./changes/change-detail').then((m) => m.ChangeDetail) },
        ],
      },
    ],
  },
];
