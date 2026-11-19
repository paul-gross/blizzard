import type { RunnerSubscription } from 'fleet';
import type { runnerApi } from 'fleet/runner-shell';

/** Copy the host's subscription facts at the runner read boundary. */
export function runnerSubscription(row: runnerApi.HostSubscriptionView): RunnerSubscription {
  return {
    slug: row.slug, name: row.name, provider: row.provider,
    condition: row.condition ?? null, miss_reason: row.miss_reason ?? null,
    ok: row.ok ?? null, sampled_at: row.sampled_at ?? null,
    windows_sampled_at: row.windows_sampled_at ?? null,
    windows: (row.windows ?? []).map(({ window, utilization_pct, resets_at, window_seconds }) =>
      ({ window, utilization_pct, resets_at, window_seconds })),
    spenders: [...(row.spenders ?? [])], renewal_result: row.renewal_result ?? null,
    renewal_failure_reason: row.renewal_failure_reason ?? null,
    renewal_attempted_at: row.renewal_attempted_at ?? null,
  };
}
