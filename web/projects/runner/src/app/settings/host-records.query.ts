import { injectQuery } from '@tanstack/angular-query-experimental';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, runnerApi } from 'fleet';

import { hostSubscriptionRecordsKey, hostWorkspaceRecordsKey } from '../core/query-keys';
import { runnerSubscription } from '../core/host-subscription.translate';
import { fromHostWorkspaceView } from '../core/host-workspace.translate';
import type { HostRecord, HostRecordKind } from './host-records.domain';
import { subscriptionRecord, workspaceRecord } from './host-records.translate';

async function fetchRecords(kind: HostRecordKind): Promise<readonly HostRecord[]> {
  const query = { retired: true };
  if (kind === 'workspace') {
    const { data, error } = await runnerApi.listWorkspacesApiHostWorkspacesGet({ query, throwOnError: false });
    if (error) throw error;
    return (data?.items ?? []).map((row) => workspaceRecord(fromHostWorkspaceView(row)));
  }
  const { data, error } = await runnerApi.listSubscriptionsApiHostSubscriptionsGet({ query, throwOnError: false });
  if (error) throw error;
  return (data?.items ?? []).map((row) => subscriptionRecord(runnerSubscription(row)));
}

/** The cache key every record of `kind` reads under — beneath the Host tab's own read of that kind. */
export function hostRecordsKeyOf(kind: HostRecordKind): readonly unknown[] {
  return kind === 'workspace' ? hostWorkspaceRecordsKey : hostSubscriptionRecordsKey;
}

/** `GET /api/host/workspaces?retired=true` or `GET /api/host/subscriptions?retired=true` — every record of `kind`
 * the host holds, the retired beside the active so one can be enabled, as `HostRecord`s through the core
 * translators. */
export function injectHostRecordsQuery(kind: () => HostRecordKind) {
  return injectQuery(() => ({
    queryKey: hostRecordsKeyOf(kind()),
    queryFn: () => fetchRecords(kind()),
    refetchInterval: RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS,
  }));
}
