import { injectInfiniteQuery, injectQuery } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, runnerApi } from 'fleet';

import { hostSettingsChangesKey, hostSettingsKey, runnerSettingsChangesKey, runnerSettingsKey } from '../core/query-keys';
import { revisionPending, type SettingsChangesPage, type SettingsTarget } from './settings.domain';
import { fromChangesPage, fromHostChangesPage, fromSettingsView } from './settings.translate';

/** How often a record holding a revision its daemon has not taken up is re-read. No live frame announces a write or
 * a take-hold, so this poll is what moves the `in_force` line. */
export const SETTINGS_PENDING_POLL_MS = 2000;

/** How many change rows one page of a change log reads. */
export const SETTINGS_CHANGES_PAGE = 20;

/** The cache key `target`'s settings record reads under; its change log reads under the same prefix. */
export function settingsKeyOf(target: SettingsTarget): readonly unknown[] {
  return target.kind === 'host' ? hostSettingsKey : runnerSettingsKey(target.runnerId);
}

/** The cache key `target`'s change log reads under. */
export function settingsChangesKeyOf(target: SettingsTarget): readonly unknown[] {
  return target.kind === 'host' ? hostSettingsChangesKey : runnerSettingsChangesKey(target.runnerId);
}

async function fetchSettings(target: SettingsTarget): Promise<runnerApi.SettingsView> {
  const { data, error } =
    target.kind === 'host'
      ? await runnerApi.getHostSettingsApiHostSettingsGet({ throwOnError: false })
      : await runnerApi.getSettingsRunnersRunnerIdApiSettingsGet({ path: { runner_id: target.runnerId }, throwOnError: false });
  if (error) throw error;
  return data!;
}

/**
 * `GET /api/host/settings` or `GET /runners/{runner_id}/api/settings` — `target`'s settings record, read as a domain
 * `SettingsRecord`. While it holds a revision its daemon has not taken up ({@link revisionPending}) it is re-read
 * every {@link SETTINGS_PENDING_POLL_MS}; otherwise on the live backstop.
 */
export function settingsQueryOptions(target: SettingsTarget) {
  return {
    queryKey: settingsKeyOf(target),
    queryFn: () => fetchSettings(target),
    select: fromSettingsView,
    refetchInterval: (query: { state: { data?: runnerApi.SettingsView } }) =>
      query.state.data !== undefined && revisionPending(fromSettingsView(query.state.data))
        ? SETTINGS_PENDING_POLL_MS
        : RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  };
}

/** {@link settingsQueryOptions} over the target `target` names. */
export function injectSettingsQuery(target: () => SettingsTarget) {
  return injectQuery(() => settingsQueryOptions(target()));
}

async function fetchChanges(target: SettingsTarget, before: number | undefined): Promise<SettingsChangesPage> {
  const query = { limit: SETTINGS_CHANGES_PAGE, before };
  if (target.kind === 'host') {
    const { data, error } = await runnerApi.hostSettingsChangesApiHostSettingsChangesGet({ query, throwOnError: false });
    if (error) throw error;
    return fromHostChangesPage(data!);
  }
  const { data, error } = await runnerApi.settingsChangesRunnersRunnerIdApiSettingsChangesGet({
    path: { runner_id: target.runnerId },
    query,
    throwOnError: false,
  });
  if (error) throw error;
  return fromChangesPage(data!);
}

/** `GET /api/host/settings/changes` or `GET /runners/{runner_id}/api/settings/changes` — `target`'s change log,
 * newest first, a page of {@link SETTINGS_CHANGES_PAGE} at a time. */
export function injectSettingsChangesQuery(target: () => SettingsTarget) {
  return injectInfiniteQuery(() => ({
    queryKey: settingsChangesKeyOf(target()),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }: { pageParam: number | undefined }) => fetchChanges(target(), pageParam),
    getNextPageParam: (last: SettingsChangesPage) => last.nextBefore ?? undefined,
  }));
}
