import { inject } from '@angular/core';
import { type CanActivateFn, Router, type UrlTree } from '@angular/router';
import { QueryClient } from '@tanstack/angular-query-experimental';
import type { runnerApi } from 'fleet';

import { fetchHostRunners } from '../core/host.query';
import { RUNNER_PARAM } from '../core/panel-filters';
import { hostRunnersKey } from '../core/query-keys';

/**
 * Redirects a `runners/:runnerId/<page>[/…]` URL — the per-runner layout's — to its flat
 * page, keeping the rest of the path and the query, the runner named in `?runner=` by its
 * name on the host. A runner the host no longer lists drops out, landing on All; a bare
 * `runners/:runnerId` lands on the board.
 */
export const legacyRunnerRedirect: CanActivateFn = async (route, state): Promise<UrlTree> => {
  const router = inject(Router);
  const queryClient = inject(QueryClient);
  const runnerId = route.paramMap.get('runnerId');
  const tree = router.parseUrl(state.url);
  const rest = (tree.root.children['primary']?.segments ?? []).slice(2).map((segment) => segment.path);
  const runners = await queryClient
    .fetchQuery({ queryKey: hostRunnersKey, queryFn: fetchHostRunners })
    .catch(() => [] as runnerApi.HostRunnerView[]);
  const name = runners.find((runner) => runner.runner_id === runnerId)?.name ?? null;
  return router.createUrlTree(['/', ...(rest.length > 0 ? rest : ['board'])], {
    queryParams: { ...tree.queryParams, [RUNNER_PARAM]: name },
    fragment: tree.fragment ?? undefined,
  });
};
