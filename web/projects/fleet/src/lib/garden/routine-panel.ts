import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitButton } from '../kit/kit-button';
import { KitFactList, type KitFact } from '../kit/kit-fact-list';
import { KitPanel } from '../kit/kit-panel';
import { KitProseBlock } from '../kit/kit-prose-block';
import { FleetWhen } from '../when-display';

/** The routine's own record (D1) — read-only: this panel ships no New/Edit
 * affordance. `RoutineView` also carries `routine_id`/`created_at`, neither
 * displayed here — identity is the list's own compact ref (`RoutineListRowVm`),
 * and `created_at` earns no row of its own on this record. */
export interface RoutineRecordVm {
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
 * The gardening routine panel's single-routine detail, as three stacked
 * `fleet-kit-panel`s (blizzard-product:/plans/garden/user-interface.md §Declaring
 * and running a routine): **Routine** — what the routine *is*, its record plus the
 * Run action; **Activity** — what it *has done*, the inflow/outflow trend,
 * measurements, and last-swept table, all runtime observations rather than routine
 * definition; and **Strategy** — the effective graph's read-only prompts.
 * Presentational only: it renders exactly the view model it is handed and injects no
 * query (D1 ships no New/Edit affordance).
 *
 * The Routine panel is the one that always renders: it wraps its own body in
 * `fleet-kit-async-state`, so the loading/error/rest states read as a panel awaiting
 * a selection rather than a bare line of text. Activity and Strategy have nothing to
 * say without a `vm()` and are absent until there is one.
 *
 * Also the panel's own Run trigger: a `run` output, emitted only while `blockedReason`
 * is unset. The container decides what running the selected routine then does — this
 * component still injects nothing. Only that action is gated on blocked-ness: a
 * routine's strategy is part of its definition, so the Strategy panel reads the same
 * whether or not a run is currently offered.
 */
@Component({
  selector: 'fleet-routine-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, FleetWhen, KitButton, KitFactList, KitPanel, KitProseBlock],
  templateUrl: './routine-panel.html',
  styleUrl: './routine-panel.css',
})
export class FleetRoutinePanel {
  readonly vm = input<RoutinePanelVm | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  readonly run = output<void>();

  /** The record as an aligned fact grid (`fleet-kit-fact-list`, `KitFact`'s own
   * shape) — a method, not a stored computed, since it depends on the selected
   * routine's record, already read off `vm()` at the one call site in the
   * template. */
  protected recordRows(record: RoutineRecordVm): readonly KitFact[] {
    return [
      { label: 'graph', value: record.graphName },
      { label: 'default scope', value: record.defaultScopeSlug },
      { label: 'default model', value: record.defaultModel.length ? record.defaultModel.join(', ') : '—' },
      { label: 'default effort', value: record.defaultEffort ?? '—' },
    ];
  }

  /** The trend's four counts as an aligned fact grid — `recordRows`' own shape. */
  protected trendRows(trend: TrendSummaryVm): readonly KitFact[] {
    return [
      { label: 'created', value: String(trend.created) },
      { label: 'outflow', value: String(trend.outflow) },
      { label: 'withdrawn', value: String(trend.withdrawn) },
      { label: 'reopened', value: String(trend.reopened) },
    ];
  }
}
