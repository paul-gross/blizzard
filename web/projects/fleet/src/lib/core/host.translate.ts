import type { RunnerConnection, SampleMissReason as HubSampleMissReason } from '../api/hub';
import type { SampleMissReason as RunnerSampleMissReason, RenewalResult as WireRenewalResult, RenewalFailureReason } from '../api/runner';
import { openLookup } from './open-lookup';
import type { HostConnection, RenewalFailure, RenewalResult, SampleMiss } from './host.domain';

const misses = openLookup<HubSampleMissReason | RunnerSampleMissReason, SampleMiss>({
  credential_lapsed: 'credential_lapsed', credential_unreadable: 'credential_unreadable',
  endpoint_unreachable: 'endpoint_unreachable', response_unparseable: 'response_unparseable',
}, (raw) => ({ kind: 'unknown', raw }));
const results = openLookup<WireRenewalResult, RenewalResult>({
  renewed: 'renewed', failed: 'failed', unrecorded: 'unrecorded',
}, (raw) => ({ kind: 'unknown', raw }));
const failures = openLookup<RenewalFailureReason, RenewalFailure>({
  renewer_unavailable: 'renewer_unavailable', timed_out: 'timed_out',
  vendor_refused: 'vendor_refused', protocol_error: 'protocol_error',
}, (raw) => ({ kind: 'unknown', raw }));
const connections = openLookup<RunnerConnection, HostConnection>({
  never_connected: 'never_connected', online: 'online', offline: 'offline',
}, (raw) => ({ kind: 'unknown', raw }));

export function sampleMiss(raw: string | null | undefined): SampleMiss | null {
  return raw == null ? null : misses(raw);
}

export function renewalResult(raw: string | null | undefined): RenewalResult | null {
  return raw == null ? null : results(raw);
}

export function renewalFailure(raw: string | null | undefined): RenewalFailure | null {
  return raw == null ? null : failures(raw);
}

export function hostConnection(raw: string): HostConnection {
  return connections(raw);
}
