import { injectInfiniteQuery, injectQuery } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, runnerApi } from 'fleet';

import { hostRecordChangesKey, hostSettingsKey, runnerSettingsChangesKey, runnerSettingsKey } from '../core/query-keys';
import { type HistoryTarget, revisionPending, type SettingsChangesPage, type SettingsTarget } from './settings.domain';
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
export function settingsChangesKeyOf(target: HistoryTarget): readonly unknown[] {
  if (target.kind === 'runner') return runnerSettingsChangesKey(target.runnerId);
  return target.kind === 'host' ? hostRecordChangesKey('host', null) : hostRecordChangesKey(target.recordKind, target.key);
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
export function settingsQueryOptions(target: SettingsTarget, enabled = true) {
  return {
    queryKey: settingsKeyOf(target),
    enabled,
    queryFn: () => fetchSettings(target),
    select: fromSettingsView,
    refetchInterval: (query: { state: { data?: runnerApi.SettingsView } }) =>
      query.state.data !== undefined && revisionPending(fromSettingsView(query.state.data))
        ? SETTINGS_PENDING_POLL_MS
        : RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  };
}

/** {@link settingsQueryOptions} over the target `target` names, read only while `enabled` holds. */
export function injectSettingsQuery(target: () => SettingsTarget, enabled: () => boolean = () => true) {
  return injectQuery(() => settingsQueryOptions(target(), enabled()));
}

async function fetchChanges(target: HistoryTarget, before: number | undefined): Promise<SettingsChangesPage> {
  const query = { limit: SETTINGS_CHANGES_PAGE, before };
  if (target.kind !== 'runner') {
    const filter =
      target.kind === 'host' ? { record_kind: 'host' } : { record_kind: target.recordKind, record_key: target.key };
    const { data, error } = await runnerApi.hostSettingsChangesApiHostSettingsChangesGet({
      query: { ...query, ...filter },
      throwOnError: false,
    });
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
 * newest first, a page of {@link SETTINGS_CHANGES_PAGE} at a time. The host's log is filtered to the record it is
 * of: the host record's own rows by `record_kind`, a workspace's or a subscription's by `record_kind` and
 * `record_key`. Read only while `enabled` holds. */
export function injectSettingsChangesQuery(target: () => HistoryTarget, enabled: () => boolean = () => true) {
  return injectInfiniteQuery(() => ({
    queryKey: settingsChangesKeyOf(target()),
    enabled: enabled(),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }: { pageParam: number | undefined }) => fetchChanges(target(), pageParam),
    getNextPageParam: (last: SettingsChangesPage) => last.nextBefore ?? undefined,
  }));
}
