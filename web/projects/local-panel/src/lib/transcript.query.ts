import { injectQuery } from '@tanstack/angular-query-experimental';
import { runnerApi } from 'fleet';

import { runnerTranscriptKey } from './query-keys';

/**
 * Runner `GET /api/leases/{lease_id}/transcript` read (issue #29) — the parsed
 * conversation transcript for one lease, spanning both active and closed leases
 * (404 only for a lease that never existed at all; a spawning agent
 * or a missing/unreadable transcript is a normal 200 with `available: false` and
 * a `reason`, not an error — the caller branches on `reason`, not on `isError()`,
 * for those cases).
 *
 * `enabled: leaseId() !== null` — no request fires until a row is selected.
 * `refetchInterval: false`, deliberately unlike {@link injectRunnerLeasesQuery}:
 * the runner has no SSE — there is no event stream to subscribe to, so
 * polling is the only signal the lease list has — but real-time transcript
 * refresh is explicitly out of scope for this issue, and polling a
 * hundreds-of-KB read every 5s is a real cost the lease list (a few hundred
 * bytes) does not pay. The query re-fires when `leaseId` changes — selecting a
 * different row — not on a timer.
 */
export function injectTranscriptQuery(leaseId: () => string | null) {
  return injectQuery(() => {
    const id = leaseId();
    return {
      queryKey: runnerTranscriptKey(id ?? ''),
      enabled: id !== null,
      queryFn: async (): Promise<runnerApi.TranscriptResponse> => {
        const { data, error } = await runnerApi.getTranscriptApiLeasesLeaseIdTranscriptGet({
          path: { lease_id: id! },
          throwOnError: false,
        });
        if (error) throw error;
        return data!;
      },
      refetchInterval: false as const,
    };
  });
}
