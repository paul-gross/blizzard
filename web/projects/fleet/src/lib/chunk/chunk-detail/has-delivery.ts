import type { ChunkDetail } from '../../api/hub';

/** Whether a chunk has anything for its delivery section to say: a PR open or closed, a landed repo, a wait on the
 * operator's merge, or a parked wait on its change's merge. */
export function hasDelivery(detail: ChunkDetail): boolean {
  return (
    detail.open_prs.length > 0 ||
    detail.closed_prs.length > 0 ||
    detail.landed_repos.length > 0 ||
    detail.awaiting_external_merge === true ||
    detail.pending?.kind === 'awaiting-merge'
  );
}
