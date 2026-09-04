import { ChangeDetectionStrategy, Component, TemplateRef, computed, input, output } from '@angular/core';

import { compactRef } from '../compact-ref';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitBadge } from '../kit/kit-badge';
import { KitButton } from '../kit/kit-button';
import { KitFactList, type KitFact } from '../kit/kit-fact-list';
import { KitProseBlock } from '../kit/kit-prose-block';
import { FleetWhen } from '../when-display';
import type { Tone } from '../kit/tone';
import { findingStateTone, isFindingExited } from './finding-state';
import type { FindingTriageVerb } from './finding-list';
import type { ProposalWorkItemVm } from './proposal-panel';

/** Every single-finding triage verb this panel can dispatch — {@link FindingTriageVerb}
 * minus `supersede`, which names a *second* finding (the absorbing one) that a
 * single-click panel button has nowhere to collect. */
export type FindingPanelTriageVerb = Exclude<FindingTriageVerb, 'supersede'>;

/** The selected finding's own panel view model (`hub finding show`'s own read) —
 * plain data, no query or wire type, `RoutinePanelVm`'s own shape.
 *
 * `introducedRev` is a **git revision**, not a timestamp — `FindingView.introduced`
 * reads e.g. `"4ba7ef06d"` against the live hub, so it renders as plain text here,
 * never through `fleet-when` (the bug this panel's own row sibling,
 * `finding-list.ts`, is dropping from its row rather than carrying forward).
 * `introducedAt` is the authored instant of that same commit, resolved only when
 * the delivery that recorded it named exactly one repository — null otherwise, which
 * the template renders as an explicit "unresolved" state beside the still-shown
 * revision, never as a blank or a dash. `firstObservedAt` is when a routine first
 * recorded the finding at all. */
export interface FindingPanelVm {
  readonly findingId: string;
  readonly findingClass: string;
  readonly locus: string;
  readonly state: string;
  readonly observedCount: number;
  readonly introducedRev: string | null;
  readonly introducedAt: string | null;
  readonly firstObservedAt: string | null;
  readonly lastSeenAt: string | null;
  readonly summary: string;
  readonly note: string | null;
  readonly workItem: ProposalWorkItemVm | null;
}

/**
 * The gardening findings tab's single-finding detail panel — the record,
 * its summary and any note as prose (`fleet-kit-prose-block`), the linked work item
 * when one names it, and the single-finding triage verbs (`finding-list.ts`'s own
 * `FindingTriageVerb`, minus `supersede`). Presentational only, no query injection:
 * the container resolves the read and wires {@link triage} to
 * `finding.mutations.ts`, `routine-panel.ts`'s own container/presentational split.
 *
 * Which verbs render depends on the finding's own state, read straight off
 * `finding-state.ts`'s own {@link isFindingExited} rather than re-derived locally:
 * the four exit verbs always render (`canControl` gating aside), and `reopen`
 * renders only once the finding has exited — resolving a finding that hasn't
 * exited is a real, always-available action; reopening one that hasn't isn't.
 */
@Component({
  selector: 'fleet-finding-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitButton, KitFactList, KitProseBlock, FleetWhen],
  templateUrl: './finding-panel.html',
  styleUrl: './finding-panel.css',
})
export class FleetFindingPanel {
  readonly vm = input<FindingPanelVm | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  /** Whether the current identity may triage this finding (`chunk:control`) —
   * `false` withholds every triage button outright. */
  readonly canControl = input(false);

  /** Emitted with the verb a triage button names — the container owns what it
   * means (`bzh:frontend-container-presentational`), routed to
   * `finding.mutations.ts` against this panel's own {@link vm}'s single finding
   * id. */
  readonly triage = output<FindingPanelTriageVerb>();

  protected readonly compactRef = compactRef;

  /** The record as an aligned fact grid (`fleet-kit-fact-list`, the one owner of
   * this chrome) — a method, not a stored computed, since it depends on both the
   * selected finding and the three `<ng-template>`s the view declares for the rows
   * whose value is markup rather than text, `routine-panel.ts`'s own `recordRows`
   * shape. */
  protected factRows(
    panel: FindingPanelVm,
    introduced: TemplateRef<unknown>,
    firstObserved: TemplateRef<unknown>,
    lastSeen: TemplateRef<unknown>,
  ): readonly KitFact[] {
    return [
      { label: 'state', value: panel.state, testid: 'fp-state' },
      { label: 'observed', value: `x${panel.observedCount}`, testid: 'fp-observed' },
      { label: 'introduced', template: introduced, testid: 'fp-introduced' },
      { label: 'first observed', template: firstObserved, testid: 'fp-first-observed' },
      { label: 'last seen', template: lastSeen, testid: 'fp-last-seen' },
    ];
  }

  protected readonly exited = computed<boolean>(() => {
    const panel = this.vm();
    return panel !== null && isFindingExited(panel.state);
  });

  /** The title's state badge tone — `finding-state.ts`'s own mapping, shared with
   * `finding-list.ts`'s row badge so the two never disagree on a state's color.
   * `idle` while nothing is selected; the badge only renders inside the `@if` that
   * already proved the view model is there. */
  protected readonly stateTone = computed<Tone>(() => {
    const panel = this.vm();
    return panel === null ? 'idle' : findingStateTone(panel.state);
  });
}
