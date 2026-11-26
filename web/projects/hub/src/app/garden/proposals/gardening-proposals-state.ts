import { Injectable, signal } from '@angular/core';

/**
 * Which proposal an open Pass or Accept dialog is closing. The selection must not move off it while the dialog is open,
 * even once it stops matching the filters: moving tears the dialog down with the operator's draft.
 */
@Injectable()
export class GardeningProposalsState {
  /** The id of the proposal an open closing dialog is for, or `null` when none is open. */
  readonly dialogProposalId = signal<string | null>(null);
}
