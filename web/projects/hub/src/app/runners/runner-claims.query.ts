import { injectQuery } from '@tanstack/angular-query-experimental';

import { LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet';
import type { BoardChunksRead } from '../core/chunks.model';
import { boardChunksQueryOptions } from '../core/chunks.query';
import type { RunnerClaimChunk } from './runner-claims.domain';
import { toRunnerClaimChunk } from './runner-claims.translate';

/**
 * The tenant's whole board list, unlensed, as the runner registry folds claims from it — the board read's own key,
 * `queryFn` and cache, projected to {@link RunnerClaimChunk}s.
 */
export function injectRunnerClaimsQuery() {
  return injectQuery(() => ({
    ...boardChunksQueryOptions(null),
    refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    select: (read: BoardChunksRead): readonly RunnerClaimChunk[] => read.chunks.map(toRunnerClaimChunk),
  }));
}
