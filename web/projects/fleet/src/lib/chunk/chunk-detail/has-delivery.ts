import type { ChunkDetail } from '../chunk-page/chunk-detail.model';

/** Whether a chunk has anything for its delivery section to say: a PR open or closed, a landed repo, a wait on the
 * operator's merge, or a parked wait on its change's merge. A pending kind this build does not know is not a parked
 * wait, so it alone gives the section nothing to say. */
export function hasDelivery(detail: Pick<ChunkDetail, 'open_prs' | 'closed_prs' | 'landed_repos' | 'awaiting_external_merge' | 'pending'>): boolean {
  return (
    detail.open_prs.length > 0 ||
    detail.closed_prs.length > 0 ||
    detail.landed_repos.length > 0 ||
    detail.awaiting_external_merge === true ||
    detail.pending?.kind.kind === 'awaiting-merge'
  );
}
