import { Injectable, signal } from '@angular/core';

/**
 * What the `/gardening/proposals` list route and its detail child share: which proposal an open Pass or Accept dialog
 * is closing. The docket drops a closing proposal from the waiting set at once, and a refused close may leave it closed
 * elsewhere; either way the selection must not move while the dialog is open, because moving it tears the dialog down
 * with the operator's draft. Provided on `GardeningProposalsPage`, so both halves resolve the same instance.
 */
@Injectable()
export class GardeningProposalsState {
  /** The id of the proposal an open closing dialog is for, or `null` when none is open. */
  readonly dialogProposalId = signal<string | null>(null);
}
