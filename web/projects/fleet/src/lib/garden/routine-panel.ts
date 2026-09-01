import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitButton } from '../kit/kit-button';
import { FleetWhen } from '../when-display';

/** The routine's own record — every field `RoutineView` carries, none it doesn't
 * (AC 1). Read-only: this panel ships no New/Edit affordance (D1). */
export interface RoutineRecordVm {
  readonly routineId: string;
  readonly name: string;
  readonly graphName: string;
  readonly defaultScopeSlug: string;
  readonly defaultModel: readonly string[];
  readonly defaultEffort: string | null;
}

/** One node of the effective graph's strategy — read-only prose (D5, D7). */
export interface StrategyStepVm {
  readonly name: string;
  readonly prompt: string | null;
}

/** The inflow/outflow reading over the panel's window (AC 3) — `created` is inflow,
 * `outflow`/`withdrawn` the two exit roll-ups, `reopened` counted on its own. */
export interface TrendSummaryVm {
  readonly created: number;
  readonly outflow: number;
  readonly withdrawn: number;
  readonly reopened: number;
}

/** One measurement inside the panel's window (D5) — opaque text, rendered as text,
 * never parsed or plotted. */
export interface MeasurementReadingVm {
  readonly scopeSlug: string;
  readonly producedAt: string;
  readonly measurement: string;
}

/** One row of the last-swept table (D3, D4) — `producedAt`/`findingSetId` are `null`
 * for a scope this routine has never swept, rendered as "never". */
export interface LastSweptRowVm {
  readonly scopeSlug: string;
  readonly findingSetId: string | null;
  readonly producedAt: string | null;
  readonly revisionsLabel: string;
}

/** The selected routine's whole panel view model (D1, D5, D7, D8) — plain data, no
 * query or wire type, so the presentational component and its spec never see one.
 * `blockedReason` alone carries blocked-ness: a non-`null` reason means blocked, so
 * there is no separate `blocked` flag that could disagree with it. */
export interface RoutinePanelVm {
  readonly record: RoutineRecordVm;
  readonly blockedReason: string | null;
  readonly strategy: readonly StrategyStepVm[];
  readonly trend: TrendSummaryVm | null;
  readonly measurements: readonly MeasurementReadingVm[];
  readonly lastSwept: readonly LastSweptRowVm[];
  readonly windowLabel: string;
}

/**
 * The gardening routine panel's single-routine detail — the record, its read-only
 * strategy, and the three health readings (blizzard-product:/plans/garden/user-
 * interface.md §Declaring and running a routine). Presentational only: it renders
 * exactly the view model it is handed and injects no query (D1 ships no New/Edit
 * affordance; every block names the CLI verb behind its own read, D8).
 *
 * Also the panel's own Run trigger: a `run` output, emitted only while `blockedReason`
 * is unset. The container decides what running the selected routine then does — this
 * component still injects nothing.
 */
@Component({
  selector: 'fleet-routine-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, FleetWhen, KitButton],
  templateUrl: './routine-panel.html',
  styleUrl: './routine-panel.css',
})
export class FleetRoutinePanel {
  readonly vm = input<RoutinePanelVm | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  readonly run = output<void>();
}
