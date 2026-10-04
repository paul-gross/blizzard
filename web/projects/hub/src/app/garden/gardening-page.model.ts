import { type GardenProposalView } from 'fleet';
import { isGardenProposalWaiting } from './proposals/gardening-proposals-page.model';

/** How many proposals are waiting on a person — the gardening strip's one urgent count. */
export function waitingProposalCount(proposals: readonly GardenProposalView[]): number {
  return proposals.filter(isGardenProposalWaiting).length;
}
