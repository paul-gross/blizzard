import { injectQuery } from '@tanstack/angular-query-experimental';

import { envTimelineStepTranscriptApiEnvTimelineStepTranscriptGet, hubEnvTimelineKey, type hubApi } from 'fleet';

/** Which session's transcript: its chunk, the node it ran, and the epoch it was fenced by. */
export interface LeaseTranscriptRef {
  readonly chunkId: string;
  readonly nodeId: string;
  readonly epoch: number;
  /** Whether the step still runs — its transcript is still growing. */
  readonly live: boolean;
}

/**
 * One session's transcript as the hub assembles it: every turn it shipped, in order — or `null` when the caller may not
 * read transcripts (the hub's `403`), which the drawer says quietly rather than as a failure.
 */
export type LeaseTranscript = Pick<hubApi.LeaseTranscriptView, 'turns' | 'truncated'> | null;

/** How long a running step's transcript reads fresh; a finished one's never changes. */
const LIVE_FRESH_MS = 15_000;

async function readLeaseTranscript(ref: LeaseTranscriptRef): Promise<LeaseTranscript> {
  const { data, error, response } = await envTimelineStepTranscriptApiEnvTimelineStepTranscriptGet({
    query: { chunk_id: ref.chunkId, node_id: ref.nodeId, epoch: ref.epoch },
    throwOnError: false,
  });
  if (response?.status === 403) return null;
  if (error) throw error;
  return data;
}

/** The transcript of the session that ran a node step; idle while there is none to read. */
export function injectLeaseTranscriptQuery(ref: () => LeaseTranscriptRef | null) {
  return injectQuery(() => {
    const r = ref();
    return {
      queryKey: [...hubEnvTimelineKey, 'transcript', r?.chunkId ?? null, r?.nodeId ?? null, r?.epoch ?? null] as const,
      queryFn: () => (r === null ? Promise.reject(new Error('no session')) : readLeaseTranscript(r)),
      enabled: r !== null,
      staleTime: r?.live ? LIVE_FRESH_MS : Infinity,
      // A transcript the caller may not read stays unread however live its step is.
      refetchInterval: (query: { state: { data: LeaseTranscript | undefined } }) =>
        r?.live && query.state.data !== null ? LIVE_FRESH_MS : false,
    };
  });
}
