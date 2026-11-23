/** Facts reported by the hub about a subscription's sampled rate-limit window. */
export interface UsageWindow {
  readonly window: string;
  readonly utilization_pct: number;
  readonly resets_at: string;
  readonly window_seconds: number;
}

/** Unrecognized labels retain their original spelling. */
export interface UnknownHostValue { readonly kind: 'unknown'; readonly raw: string }
export type KnownSampleMiss = 'credential_lapsed' | 'credential_unreadable' | 'endpoint_unreachable' | 'response_unparseable';
export type SampleMiss = KnownSampleMiss | UnknownHostValue;

export type RenewalResult = 'renewed' | 'failed' | 'unrecorded' | UnknownHostValue;
export type KnownRenewalFailure = 'renewer_unavailable' | 'timed_out' | 'vendor_refused' | 'protocol_error';
export type RenewalFailure = KnownRenewalFailure | UnknownHostValue;
export type HostConnection = 'never_connected' | 'online' | 'offline' | UnknownHostValue;

/** Runner-host subscription facts, independent of its transport. */
export interface RunnerSubscription {
  readonly slug: string;
  readonly name: string;
  readonly provider: string;
  readonly condition: SampleMiss | null;
  readonly miss_reason: SampleMiss | null;
  readonly ok: boolean | null;
  readonly sampled_at: string | null;
  readonly windows_sampled_at: string | null;
  readonly windows: readonly UsageWindow[];
  readonly spenders: readonly string[];
  readonly renewal_result: RenewalResult | null;
  readonly renewal_failure_reason: RenewalFailure | null;
  readonly renewal_attempted_at: string | null;
}

export interface SubscriptionUsage {
  readonly slug: string;
  readonly name: string;
  readonly windows: readonly UsageWindow[];
  readonly sampled_at: string | null;
  readonly missed_at?: string | null;
  readonly condition: SampleMiss | null;
  readonly miss_reason: SampleMiss | null;
}

export interface HostUsage extends SubscriptionUsage {
  readonly runners: readonly string[];
}

export interface HostWorkspaceFact {
  readonly workspace_id: string;
  readonly env_capacity?: number | null;
}

/** Host registry facts shared by fleet views; no display formatting is stored here. */
export interface HostFact {
  readonly host_id: string;
  readonly host_name: string;
  readonly online: boolean;
  readonly connection: HostConnection;
  readonly last_seen_at: string | null;
  readonly url: string | null;
  readonly urls: readonly string[];
  readonly enrolled: boolean;
  readonly registered_at: string | null;
  readonly added_at: string;
  readonly added_by: string | null;
  readonly retired: boolean;
  readonly runner_ids: readonly string[];
  readonly workspaces: readonly HostWorkspaceFact[];
  readonly subscription_usage: readonly HostUsage[];
  readonly subscriptions: readonly { readonly slug: string; readonly name: string }[];
}
