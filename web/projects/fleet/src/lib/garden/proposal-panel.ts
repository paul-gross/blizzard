import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { compactRef } from '../compact-ref';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitButton } from '../kit/kit-button';
import { KitFactList, type KitFact } from '../kit/kit-fact-list';
import { KitPanel } from '../kit/kit-panel';
import { KitProseBlock } from '../kit/kit-prose-block';
import { FleetWhen } from '../when-display';

/** A linked hub work item, legible for display — `label`/`webUrl` come straight off
 * `WorkItemView` rather than being guessed; `webUrl` is `null` once the chunk is
 * terminal (Decision 4), so the caller renders `label` alone instead of a dead
 * link. */
export interface ProposalWorkItemVm {
  readonly label: string;
  readonly webUrl: string | null;
}

/** One evidence row — a live-read finding, never a copy the proposal itself
 * carries (Decision 3). `workItem` repeats the same accepted-and-minted proposal's
 * work item on every one of its finding rows, `null` otherwise. */
export interface ProposalEvidenceRowVm {
  readonly findingId: string;
  readonly locus: string;
  readonly summary: string;
  readonly live: boolean;
  readonly workItem: ProposalWorkItemVm | null;
}

/** How a proposal closed, rendered as the record it is (the docket's two closing
 * verbs) — `'accepted'` with a `null` `workItem` is the acceptance that says on the
 * record it minted nothing (Decision 5), never an empty space where the item would
 * be. */
export type ProposalClosureVm =
  | { readonly kind: 'passed'; readonly closedBy: string; readonly closedAt: string; readonly reason: string | null }
  | {
      readonly kind: 'accepted';
      readonly closedBy: string;
      readonly closedAt: string;
      readonly reason: string | null;
      readonly workItem: ProposalWorkItemVm | null;
    };

/** The selected proposal's whole panel view model — plain data, no query or wire
 * type, `RoutinePanelVm`'s own shape. `closure` is `null` while the proposal is
 * still waiting. */
export interface ProposalPanelVm {
  readonly proposalId: string;
  readonly routineName: string;
  readonly proposalClass: string;
  readonly title: string;
  readonly body: string;
  readonly closure: ProposalClosureVm | null;
  readonly createdAt: string;
}

/**
 * The garden proposal docket's detail panel — the case as prose, the closure record
 * once one exists, and the evidence table (`blizzard-product:/plans/garden/user-
 * interface.md` §The docket). Presentational only: renders exactly the view model
 * and evidence rows it is handed and injects no query of its own — the container
 * resolves the live finding reads and the accepted work item read
 * (`bzh:frontend-container-presentational`).
 *
 * Copy states plainly that acceptance neither promotes the minted item nor changes
 * any finding's state — the surface's own answer to the two things acceptance does
 * not do.
 *
 * A still-waiting proposal (`vm.closure === null`) offers Pass and Accept, each
 * naming the CLI verb behind it — withheld without `chunk:control` (Decision 6), the
 * same permission the closing routes themselves require, via the `canControl` input
 * a viewer identity resolves to `false`.
 */
@Component({
  selector: 'fleet-proposal-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, FleetWhen, KitButton, KitFactList, KitPanel, KitProseBlock, RouterLink],
  templateUrl: './proposal-panel.html',
  styleUrl: './proposal-panel.css',
})
export class FleetProposalPanel {
  readonly vm = input<ProposalPanelVm | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  readonly evidence = input<readonly ProposalEvidenceRowVm[]>([]);
  readonly evidenceState = input<KitAsyncStateValue>('empty');

  /** Whether the current identity may pass or accept (`chunk:control`) — `false`
   * withholds both triggers outright rather than offering a button that 403s. */
  readonly canControl = input(false);

  readonly pass = output<void>();
  readonly accept = output<void>();

  protected readonly compactRef = compactRef;

  /** The pass/accept CLI hints as an aligned fact grid, above the action bar
   * (`fleet-kit-fact-list`, `KitFact`'s own shape) — a method, not a stored
   * computed, since it depends on the still-waiting proposal's id, already read
   * off `vm()` at the one call site in the template. */
  protected cliRows(proposalId: string): readonly KitFact[] {
    return [
      { label: 'pass', value: `hub garden-proposal pass ${proposalId}` },
      { label: 'accept', value: `hub garden-proposal accept ${proposalId}` },
    ];
  }
}
