import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { compactRef, type hubApi, KitAsyncState, type KitAsyncStateValue, KitBadge, KitButton, KitFactList, type KitFact, KitPanel, KitProseBlock, type Tone, FleetWhen } from 'fleet';
import type { FindingTriageVerb } from './finding-list';
import { findingStateTone, isFindingExited } from './finding-state';

/** A linked hub work item, legible for display — `label`/`webUrl` come straight off
 * `WorkItemView` rather than being guessed; `webUrl` is `null` once the chunk is
 * terminal, so the caller renders `label` alone instead of a dead
 * link. */
export interface ProposalWorkItemVm {
  readonly label: string;
  readonly webUrl: string | null;
}

/** One evidence row — a live-read finding, never a copy the proposal itself
 * carries. `workItem` repeats the same accepted-and-minted proposal's
 * work item on every one of its finding rows, `null` otherwise.
 *
 * `state` tints the row's badge and `exit` alone decides whether it has exited;
 * pinned by `proposal-panel.spec.ts`'s "still offers every verb on a gone-flagged
 * row, which has not exited". */
export interface ProposalEvidenceRowVm {
  readonly findingId: string;
  readonly locus: string;
  readonly summary: string;
  readonly state: hubApi.FindingState;
  /** `FindingView.exit` — non-null once the finding has exited. */
  readonly exit: hubApi.FindingExit | null;
  readonly workItem: ProposalWorkItemVm | null;
  /** Whether an exit verb is in flight for this finding — disables the row's triage
   * buttons so it cannot be triaged again before the first request settles. */
  readonly pending: boolean;
}

/** The exit verbs the evidence table dispatches inline — {@link FindingTriageVerb}
 * minus `supersede`, which needs an absorbing finding this row has no way to name,
 * and minus `reopen`, which is not a way to clear a row off a docket. */
export type ProposalEvidenceVerb = Extract<
  FindingTriageVerb,
  'resolve' | 'confirm-gone' | 'wont-fix' | 'not-a-finding'
>;

/** One inline triage the evidence table asks for. The panel names the finding and
 * the verb and stops there: the mutation, the note it carries, and the permission
 * re-check are the container's (`bzh:frontend-container-presentational`). */
export interface ProposalEvidenceTriage {
  readonly findingId: string;
  readonly verb: ProposalEvidenceVerb;
}

/** The inline actions, in the order they render. Labels match the triage dialog's own
 * `VERB_LABELS` so one verb is not two different words across two surfaces. */
export const PROPOSAL_EVIDENCE_ACTIONS: readonly { readonly verb: ProposalEvidenceVerb; readonly label: string }[] = [
  { verb: 'resolve', label: 'Resolve' },
  { verb: 'confirm-gone', label: 'Confirm gone' },
  { verb: 'wont-fix', label: "Won't fix" },
  { verb: 'not-a-finding', label: 'Not a finding' },
];

/** How a proposal closed, rendered as the record it is (the docket's two closing
 * verbs) — `'accepted'` with a `null` `workItem` is the acceptance that says on the
 * record it minted nothing, never an empty space where the item would
 * be. */
export type ProposalClosureVm =
  | { readonly kind: Extract<hubApi.GardenProposalClosureKind, 'passed'>; readonly closedBy: string; readonly closedAt: string; readonly reason: string | null }
  | {
      readonly kind: Extract<hubApi.GardenProposalClosureKind, 'accepted'>;
      readonly closedBy: string;
      readonly closedAt: string;
      readonly reason: string | null;
      readonly workItem: ProposalWorkItemVm | null;
    };

/** Who authored a proposal — `routine-run` names the routine that
 * raised it; `operator` names the authoring identity, plus the routine when one was
 * named, since an operator proposal may cite none at all. */
export type ProposalOriginVm =
  | { readonly kind: Extract<hubApi.GardenProposalOrigin, 'routine-run'>; readonly routineName: string }
  | { readonly kind: Extract<hubApi.GardenProposalOrigin, 'operator'>; readonly createdBy: string; readonly routineName: string | null };

/** The selected proposal's whole panel view model — plain data, no query or wire
 * type, `RoutinePanelVm`'s own shape. `closure` is `null` while the proposal is
 * still waiting. */
export interface ProposalPanelVm {
  readonly proposalId: string;
  readonly origin: ProposalOriginVm;
  readonly proposalClass: string;
  readonly title: string;
  readonly body: string;
  readonly closure: ProposalClosureVm | null;
  readonly createdAt: string;
  /** Whether the proposal cites any findings — gates the Evidence section's presence. */
  readonly hasFindings: boolean;
}

/**
 * The garden proposal docket's detail panel — the case as prose, the closure record
 * once one exists, and the evidence table (`blizzard-product:/delivered/garden/user-
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
 * naming the CLI verb behind it — withheld without `chunk:control`, the
 * same permission the closing routes themselves require, via the `canControl` input
 * a viewer identity resolves to `false`.
 */
@Component({
  selector: 'app-proposal-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, FleetWhen, KitBadge, KitButton, KitFactList, KitPanel, KitProseBlock, RouterLink],
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

  /** An inline exit verb asked for on one evidence row. */
  readonly evidenceTriage = output<ProposalEvidenceTriage>();

  protected readonly compactRef = compactRef;
  protected readonly actions = PROPOSAL_EVIDENCE_ACTIONS;

  /** The case header's origin fragment — the routine name for a `routine-run`
   * proposal, or `operator · <createdBy>` with the routine named alongside when an
   * operator proposal cites one. */
  protected originLabel(origin: ProposalOriginVm): string {
    if (origin.kind === 'routine-run') return origin.routineName;
    return origin.routineName === null ? `operator · ${origin.createdBy}` : `operator · ${origin.createdBy} · ${origin.routineName}`;
  }

  /** Whether the row has already exited, classified off the wire `exit` through the
   * shared predicate — the same one `finding-panel.ts` gates its own verbs on, so the
   * two surfaces never disagree about what a finding may still be triaged into. */
  protected readonly isExited = isFindingExited;

  /** The shared finding-state palette (`finding-state.ts`), so an evidence row and
   * the finding's own panel never colour the same state differently. */
  protected stateTone(row: ProposalEvidenceRowVm): Tone {
    return findingStateTone(row.state);
  }

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
