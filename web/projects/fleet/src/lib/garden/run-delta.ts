import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { compactRef } from '../compact-ref';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitProseBlock } from '../kit/kit-prose-block';
import { FleetWhen } from '../when-display';

/** One `add` op a delivered set's artifact published — `findingClass` rather than
 * the wire's `class` (`AddedFindingView.class`), so a template never confuses it
 * with the DOM `class` attribute. `findingId` is `null` on a set predating the
 * finding linkage, in which case the ref renders as plain unlinked text rather
 * than a dead link. */
export interface RunDeltaAddedFindingVm {
  readonly findingId: string | null;
  readonly findingClass: string;
  readonly locus: string;
  readonly summary: string;
  readonly introduced: string | null;
}

/** One `observed` op a delivered set's artifact named. The artifact repeats no
 * descriptive field for a finding it is merely re-observing, so the three below are
 * read back from the finding row the id names — each `null` when the id names no
 * row, which renders as the id alone rather than dropping the entry. */
export interface RunDeltaObservedFindingVm {
  readonly findingId: string;
  readonly findingClass: string | null;
  readonly locus: string | null;
  readonly summary: string | null;
}

/** One `gone` op a delivered set's artifact published. */
export interface RunDeltaGoneFindingVm {
  readonly findingId: string;
  readonly note: string;
}

/** One delivered set's own published delta — added, observed, and gone kept as three
 * distinct groups, never merged with another set's; a group with no entries is
 * hidden rather than rendered as an empty "none" block. */
export interface RunDeltaSetVm {
  readonly findingSetId: string;
  readonly revisionsLabel: string;
  readonly measurement: string | null;
  readonly added: readonly RunDeltaAddedFindingVm[];
  readonly observed: readonly RunDeltaObservedFindingVm[];
  readonly gone: readonly RunDeltaGoneFindingVm[];
}

/** The escalated run's open escalation — `null` on any other outcome. */
export interface RunDeltaEscalationVm {
  readonly nodeName: string | null;
  readonly takeoverCommand: string;
  readonly wrappedTakeoverCommand: string;
}

/** One run's own delta view model — plain data, no query or wire type, so this
 * presentational component and its spec never see one. `mintedAt` is `null` when
 * the container has no matching run-list row to source it from. */
export interface RunDeltaVm {
  readonly chunkId: string;
  readonly routineName: string;
  readonly scopeSlug: string;
  readonly mintedAt: string | null;
  readonly escalation: RunDeltaEscalationVm | null;
  readonly sets: readonly RunDeltaSetVm[];
}

/** `/gardening/findings/:findingId` — every finding id this panel
 * links (an added entry's `findingId`, an observed entry's, a gone entry's)
 * routes here, `RouterLink` rather than a click handler so middle-click and
 * open-in-new-tab both work. */
function findingRoute(findingId: string): readonly string[] {
  return ['/gardening', 'findings', findingId];
}

/**
 * The gardening runs tab's run delta — presentational only, no query injection:
 * one run's added/observed/gone, grouped three ways. Several delivered sets each keep
 * their own added/observed/gone block — two sets' groups are never merged into one.
 *
 * The header meta line matches `proposal-panel.html`'s own shape: one uniform,
 * `·`-separated sequence. The chunk id is the one element that differs from its
 * neighbours — it is a link (`routerLink` to the board's chunk detail), styled
 * through `.rd-ref`, the same reference-link color every other id this panel
 * names (a finding-set heading, an added/observed/gone finding) also uses.
 */
@Component({
  selector: 'fleet-run-delta',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitProseBlock, RouterLink, FleetWhen],
  templateUrl: './run-delta.html',
  styleUrl: './run-delta.css',
})
export class FleetRunDelta {
  readonly vm = input<RunDeltaVm | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  protected readonly compactRef = compactRef;
  protected readonly findingRoute = findingRoute;
}
