import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { compactRef } from '../compact-ref';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitBadge } from '../kit/kit-badge';
import { KitSelectRow } from '../kit/kit-select-row';
import { FleetWhen } from '../when-display';
import type { Tone } from '../kit/tone';
import { findingStateTone, isFindingExited, isFindingGoneFlagged } from './finding-state';

/** One row of the findings triage bucket list (`hub finding list`'s own read),
 * pared to what a 320px master-column row actually shows — the summary as the
 * row's own headline, the class and compact ref, the locus, and the most recent
 * observation (`lastSeenAt`). `observed_count` and `introduced` stay off this row
 * (they render on `finding-panel.ts`'s own `FindingPanelVm`, the right-hand detail
 * pane a row click opens) — `findingClass` renames the wire's `class`,
 * `RunDeltaVm`'s own `AddedFindingView.class` → `findingClass` rename, so a
 * template never confuses it with the DOM `class` attribute. `state` alone still
 * rides the row purely for classification — {@link FleetFindingList.isGone}/
 * {@link FleetFindingList.isExited} need it even though the row no longer prints
 * it as text. */
export interface FindingListRowVm {
  readonly findingId: string;
  readonly findingClass: string;
  readonly locus: string;
  readonly summary: string;
  readonly state: string;
  readonly lastSeenAt: string | null;
}

/** The triage verbs a finding can be dispatched under — every human-driven exit
 * `finding.mutations.ts` exposes, plus `reopen`. Named off the CLI's own verb
 * spelling (`src/blizzard/hub/cli/finding.py`), so a container routes an emitted
 * verb straight to the matching mutation with no translation table of its own.
 * Dispatched one finding at a time, from `fleet-finding-panel`'s own `triage`
 * output — this list renders rows only, it carries no selection or bulk action of
 * its own. */
export type FindingTriageVerb = 'resolve' | 'confirm-gone' | 'wont-fix' | 'not-a-finding' | 'supersede' | 'reopen';

/**
 * The gardening findings tab's findings triage list — presentational only, no
 * query injection, `run-list.ts`'s own shape: renders the rows it is handed,
 * exactly as filtered by the container (D3: class/state filtering happens
 * client-side, this component stays dumb over whatever `rows()` it's given).
 *
 * Built on `fleet-kit-select-row`: a row click emits {@link findingPick}, opening
 * that finding in the right-hand `fleet-finding-panel` — the container turns the
 * picked id into a route navigation, `run-list.ts`'s own `runPick` shape. Triage
 * itself is single-finding only, dispatched from the panel a row click opens, not
 * from this list.
 *
 * A row's own `state` decides its tint (`finding-state.ts`'s own three-way
 * classification), carried on the projected `.fl-body` div rather than the kit row's
 * own encapsulated button — `run-list.ts`'s own `.rl-body`/`.rl-body--escalated`
 * shape and its doc comment on why: still open with no flag renders untinted; a
 * `gone`-flagged row (D8) renders tinted (`.fl-body--gone`) but stays a normal,
 * fully rendered row — `gone` is *not* exited; an exited row (one of
 * `finding-state.ts`'s `FINDING_EXIT_STATES`) renders dimmed (`.fl-body--exited`)
 * but never leaves the DOM.
 *
 * The row's state rides the last-seen line, pushed to that line's right edge — the
 * row is otherwise silent about whether a finding is still open, resolved, or
 * withdrawn, and that is the first thing a triage pass needs to see. It renders as
 * `fleet-kit-badge`'s soft pill (`bzh:frontend-kit-floor`), the board's own pill
 * vocabulary, tinted by `finding-state.ts`'s shared mapping. The row's own
 * gone/exited *tint* stays alongside it rather than being replaced by it: the tint
 * classifies three broad buckets at a glance, the badge names the exact state.
 *
 * The row's own headline (`.fl-summary`) is the finding's summary itself, clamped
 * to three lines — `proposal-list.ts`'s own `.pl-title` shape, so the two lists read
 * alike. It renders as plain clamped text, never through `fleet-kit-prose-block`:
 * that kit's transcript rail and `pre-wrap` body fight a line clamp, and the full
 * prose already has a home once a row is picked — `finding-panel.ts`'s own
 * `fp-summary`.
 */
@Component({
  selector: 'fleet-finding-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitSelectRow, FleetWhen],
  templateUrl: './finding-list.html',
  styleUrl: './finding-list.css',
})
export class FleetFindingList {
  readonly rows = input.required<readonly FindingListRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();

  /** The finding currently open in the right-hand panel, `run-list.ts`'s own
   * `selectedChunkId` shape. */
  readonly selectedId = input<string | null>(null);

  /** Emitted with the id of the row a click picked. */
  readonly findingPick = output<string>();

  protected readonly compactRef = compactRef;

  protected isGone(row: FindingListRowVm): boolean {
    return isFindingGoneFlagged(row.state);
  }

  protected isExited(row: FindingListRowVm): boolean {
    return isFindingExited(row.state);
  }

  /** The row's state badge tone — `finding-state.ts`'s own mapping, so this row and
   * `finding-panel.ts`'s own title badge cannot disagree on a state's color. */
  protected stateTone(row: FindingListRowVm): Tone {
    return findingStateTone(row.state);
  }
}
