import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { ChunkStatus } from '../api/hub';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitBadge } from '../kit/kit-badge';
import { STATUS_TONE } from '../chunk-lanes';
import { FleetWhen } from '../when-display';

/** One finding-set row a run delivered (D4: several sets from one run stay separately
 * listed here, never merged into one). */
export interface RunListDeliveredSetVm {
  readonly findingSetId: string;
  readonly revisionsLabel: string;
  readonly measurement: string | null;
}

/** One row of the run list — every field `RunRowView` carries (`hub run list`'s own
 * read), plus `escalated`, folded once here so the row and its distinct styling never
 * disagree on what counts as escalated (an open escalation, `RunRowView.escalation`
 * non-`null`). */
export interface RunListRowVm {
  readonly chunkId: string;
  readonly routineName: string;
  readonly scopeSlug: string;
  readonly mode: string;
  readonly mintedAt: string;
  readonly outcome: ChunkStatus;
  readonly escalated: boolean;
  readonly delivered: readonly RunListDeliveredSetVm[];
}

/**
 * The gardening runs-and-findings tab's run list (blizzard#397 Phase 3) —
 * presentational only, no query injection: renders the rows it is handed, colors each
 * row's outcome by the fleet-wide {@link STATUS_TONE} vocabulary (rather than a second
 * status→color mapping), and keeps every delivered finding-set its own visible
 * sub-entry (D4) instead of collapsing several sets into one line. An escalated row
 * (`escalated`) renders with its own distinct treatment, not merely the outcome
 * badge's own red — the run-list shell sweep (`garden-runs.shell-sweep.spec.ts`)
 * proves that treatment is a real computed style, not just a class name.
 *
 * Emits `runPick`, `routine-list.ts`'s `routinePick` naming (`@angular-eslint/no-
 * output-native` forbids an output named `select`); the container turns the picked
 * chunk id into a route navigation.
 */
@Component({
  selector: 'fleet-run-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, FleetWhen],
  templateUrl: './run-list.html',
  styleUrl: './run-list.css',
})
export class FleetRunList {
  readonly rows = input.required<readonly RunListRowVm[]>();
  readonly selectedChunkId = input<string | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  readonly runPick = output<string>();

  protected readonly STATUS_TONE = STATUS_TONE;

  protected pick(chunkId: string): void {
    this.runPick.emit(chunkId);
  }
}
