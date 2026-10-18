import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { compactRef, type hubApi, KitAsyncState, type KitAsyncStateValue, KitBadge, KitSelectRow, FleetWhen, type Tone } from 'fleet';
import { findingSeverityTone, findingStateTone, isFindingExited, isFindingGoneFlagged } from './finding-state';
import { LensLink } from 'fleet/shell';

/** One row of the findings triage bucket list, pared to what a 320px
 * master-column row shows. `findingClass` renames the wire's `class` so a
 * template never confuses it with the DOM `class` attribute. */
export interface FindingListRowVm {
  readonly findingId: string;
  readonly findingClass: string;
  readonly locus: string;
  readonly summary: string;
  readonly state: hubApi.FindingState;
  /** `FindingView.exit` — `outflow` or `withdrawn` once the finding has exited,
   * `null` while it is still open. */
  readonly exit: hubApi.FindingExit | null;
  readonly lastSeenAt: string | null;
  /** The finding's own routine/scope, rendered only when non-null. */
  readonly routineName: string | null;
  readonly scopeSlug: string | null;
  /** `FindingView.source` — `"routine"` or `"review"`. A
   * review-sourced finding carries no {@link routineName} (always `null` for one),
   * so the row shows {@link severity} and {@link raisedByChunkId} in its place
   * rather than leaving that slot silently empty. */
  readonly source: hubApi.FindingSource;
  /** `FindingView.severity` — set only when {@link source} is `"review"`. */
  readonly severity: hubApi.FindingSeverity | null;
  /** `FindingView.raised_by_chunk_id` — the chunk id whose review raised the
   * finding, set only when {@link source} is `"review"`. */
  readonly raisedByChunkId: string | null;
}

/** The triage verbs a finding can be dispatched under, spelled as the `hub finding` CLI spells them. */
export type FindingTriageVerb = 'resolve' | 'confirm-gone' | 'wont-fix' | 'not-a-finding' | 'supersede' | 'reopen';

/**
 * The gardening findings tab's findings triage list — presentational only:
 * renders the rows it is handed, exactly as filtered by the container.
 *
 * Built on `fleet-kit-select-row`: a row click emits {@link findingPick}.
 *
 * A row's `state` decides its tint, carried on the projected `.fl-body` div: open
 * renders untinted, a `gone`-flagged row renders tinted (`.fl-body--gone`), an
 * exited row (a non-null wire `exit`) renders dimmed (`.fl-body--exited`) but never
 * leaves the DOM. The state also renders as a `fleet-kit-badge` on the last-seen
 * line, tinted by `finding-state.ts`.
 *
 * The row's own headline (`.fl-summary`) is the finding's summary as plain text
 * clamped to three lines — pinned by `finding-list.spec.ts`'s "clamps the summary
 * headline to three lines, never through fleet-kit-prose-block" and
 * `gardening-findings-triage.shell-sweep.spec.ts`'s "genuinely clamps a long summary
 * to three lines…".
 */
@Component({
  selector: 'app-finding-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitSelectRow, FleetWhen, RouterLink, LensLink],
  templateUrl: './finding-list.html',
  styleUrl: './finding-list.css',
})
export class FleetFindingList {
  readonly rows = input.required<readonly FindingListRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();

  /** The finding currently open in the right-hand panel. */
  readonly selectedId = input<string | null>(null);

  /** Emitted with the id of the row a click picked. */
  readonly findingPick = output<string>();

  protected readonly compactRef = compactRef;

  protected isGone(row: FindingListRowVm): boolean {
    return isFindingGoneFlagged(row.state);
  }

  protected isExited(row: FindingListRowVm): boolean {
    return isFindingExited(row.exit);
  }

  /** The row's state badge tone, per `findingStateTone`. */
  protected stateTone(row: FindingListRowVm): Tone {
    return findingStateTone(row.state);
  }

  /** The row's severity badge tone, when the row carries a severity. */
  protected severityTone(row: FindingListRowVm): Tone {
    return row.severity === null ? 'idle' : findingSeverityTone(row.severity);
  }
}
