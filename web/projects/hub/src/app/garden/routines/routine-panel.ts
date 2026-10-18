import { ChangeDetectionStrategy, Component, type TemplateRef, input, output, signal } from '@angular/core';

import {
  compactRef,
  FleetWhen,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBadge,
  KitButton,
  KitConfirmDialog,
  type KitFact,
  KitFactList,
  KitPanel,
  KitProseBlock,
  KitTextInput,
} from 'fleet';

import { lifecycleTone } from '../../core/lifecycle-tone';

/** The routine's own record — its graph alone is editable here. `RoutineView` also carries `routine_id`/`created_at`, neither
 * displayed here — identity is the list's own compact ref (`RoutineListRowVm`),
 * and `created_at` earns no row of its own on this record. */
export interface RoutineRecordVm {
  readonly name: string;
  readonly graphName: string;
  readonly defaultScopeSlug: string;
  /** The default model and effort as one fact, `opus · high`. */
  readonly modelEffort: string;
}

/** One of the routine's runs in the runs window, newest first. */
export interface RecentRunVm {
  readonly chunkId: string;
  readonly scopeSlug: string;
  readonly mode: string;
  /** How many findings the run's delivered sets added. */
  readonly findings: number;
  readonly mintedAt: string;
}

/** One node of the effective graph's strategy — read-only prose. */
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

/** One measurement inside the panel's window — opaque text, rendered as text,
 * never parsed or plotted. */
export interface MeasurementReadingVm {
  readonly scopeSlug: string;
  readonly producedAt: string;
  readonly measurement: string;
}

/** One row of the last-swept table — `producedAt`/`findingSetId` are `null`
 * for a scope this routine has never swept, rendered as "never". */
export interface LastSweptRowVm {
  readonly scopeSlug: string;
  readonly findingSetId: string | null;
  readonly producedAt: string | null;
  readonly revisionsLabel: string;
}

/** One scope related to the selected routine — `isDefault` marks whether this scope
 * is the routine's own default, `RelatedRoutineVm`'s own shape (`scope-panel.ts`). */
export interface RelatedScopeVm {
  readonly slug: string;
  readonly isDefault: boolean;
}

/** The selected routine's whole panel view model — plain data, no
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
  /** Every scope linked to this routine, each marked whether it is the routine's
   * own default — `null` while the relation read is still pending, `trend`'s
   * own nullable-secondary-read shape. */
  readonly relatedScopes: readonly RelatedScopeVm[] | null;
  /** The routine's real, unoverridden lifecycle flag — which control
   * renders (Retire/Re-enable) stays keyed off this, never off {@link
   * renderedRetired} (`ScopePanelVm.retired`'s own shape), so a pending lifecycle
   * mutation's own predicted outcome can never flip which verb the next click would
   * fire. */
  readonly retired: boolean;
  /** The lifecycle badge's rendered value, and what hides the Run action, merged with
   * any currently pending Retire/Enable's own predicted outcome
   * (`bzh:frontend-pending-override`, `ScopePanelVm.renderedRetired`'s own shape). */
  readonly renderedRetired: boolean;
  /** The routine's runs in the runs window, newest first — `null` while the runs read is pending. */
  readonly recentRuns: readonly RecentRunVm[] | null;
  /** The recent-runs section's own async state, gated on the runs read (`bzh:frontend-empty-state-gated`). */
  readonly recentRunsState: KitAsyncStateValue;
  /** The measurement section's own async state, gated on the sweeps read (`bzh:frontend-empty-state-gated`). */
  readonly measurementsState: KitAsyncStateValue;
  /** The Runs fact: how many runs the window holds, labelled with the window — `null` while the runs read is pending. */
  readonly runsLabel: string | null;
}

/**
 * The gardening routine panel's single-routine detail, as three stacked
 * `fleet-kit-panel`s (`blizzard-product:/delivered/garden/user-interface.md` §Declaring
 * and running a routine): **Routine** — what the routine *is*: a header holding its
 * name, project chip and lifecycle with Run…, Edit and Retire at the right, its record as a
 * fact list, and its recent runs in the runs window; **Activity** — what it *has done*, the inflow/outflow trend,
 * measurements, and last-swept table, all runtime observations rather than routine
 * definition; and **Strategy** — the effective graph's read-only prompts.
 * Presentational only: it renders exactly the view model it is handed and injects no
 * query.
 *
 * The Routine panel is the one that always renders: it wraps its own body in
 * `fleet-kit-async-state`, so the loading/error/rest states read as a panel awaiting
 * a selection rather than a bare line of text. Activity and Strategy have nothing to
 * say without a `vm()` and are absent until there is one.
 *
 * Also the panel's own Run trigger: a `run` output, emitted only while `blockedReason`
 * is unset and the routine is not (rendered-)retired. The container decides what
 * running the selected routine then does — this component still injects nothing. Only
 * that action is gated on blocked-ness/retirement: a routine's strategy is part of its
 * definition, so the Strategy panel reads the same whether or not a run is currently
 * offered.
 *
 * Edit opens the graph in place; Set emits the new graph name, and the editor closes
 * once the new graph reads back — `FleetScopePanel`'s own description editor.
 *
 * Retire/enable confirm before emitting — `FleetScopePanel`'s own confirm-then-emit
 * pattern.
 */
@Component({
  selector: 'app-routine-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, FleetWhen, KitBadge, KitButton, KitConfirmDialog, KitFactList, KitPanel, KitProseBlock, KitTextInput],
  templateUrl: './routine-panel.html',
  styleUrl: './routine-panel.css',
})
export class FleetRoutinePanel {
  readonly vm = input<RoutinePanelVm | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  /** The project the routine belongs to, shown beside its name; `null` until read. */
  readonly project = input<string | null>(null);

  /** Whether the current identity may edit or retire/enable this routine (`graph:edit`) —
   * withholds Edit and the lifecycle controls when `false`, `FleetScopePanel.canEdit`'s own
   * shape. */
  readonly canEdit = input(false);

  /** Set on a failed edit/retire/enable; rendered beside the controls that raise it. */
  readonly actionError = input<string | null>(null);

  /** Whether the retire/enable mutation is in flight — disables both Re-enable and
   * Retire, since only one is ever shown for this routine's current lifecycle
   * state. */
  readonly lifecyclePending = input(false);

  /** Whether the graph edit is in flight — disables the Set button. */
  readonly editPending = input(false);

  readonly run = output<void>();
  readonly editGraph = output<string>();
  readonly retire = output<string>();
  readonly enable = output<string>();

  /** The lifecycle badge's tone, from the hub's one lifecycle owner. */
  protected readonly lifecycleTone = lifecycleTone;

  protected readonly pendingConfirm = signal<{
    readonly heading: string;
    readonly message: string;
    readonly confirmLabel: string;
    readonly variant: 'primary' | 'danger';
    readonly run: () => void;
  } | null>(null);

  /** The routine whose graph is open for editing, with the graph it opened on, and the draft typed so far — `null`
   * while none is. Keyed by name so a pick of another routine never carries an open editor across, and by the graph
   * so a saved edit, reading back a new one, closes the editor while a failed one leaves it open beside its error. */
  private readonly editing = signal<{ readonly name: string; readonly original: string; readonly value: string } | null>(
    null,
  );

  protected isEditing(panel: RoutinePanelVm): boolean {
    const editing = this.editing();
    return editing?.name === panel.record.name && editing.original === panel.record.graphName;
  }

  protected startEditing(panel: RoutinePanelVm): void {
    const { name, graphName } = panel.record;
    this.editing.set({ name, original: graphName, value: graphName });
  }

  protected stopEditing(): void {
    this.editing.set(null);
  }

  protected graphDraft(panel: RoutinePanelVm): string {
    return this.isEditing(panel) ? (this.editing()?.value ?? panel.record.graphName) : panel.record.graphName;
  }

  protected updateGraphDraft(panel: RoutinePanelVm, value: string): void {
    this.editing.set({ name: panel.record.name, original: panel.record.graphName, value });
  }

  /** The record as an aligned fact grid (`fleet-kit-fact-list`, `KitFact`'s own
   * shape) — a method, not a stored computed, since it depends on the selected
   * routine's record and on the view's own templates for the rows whose value is
   * markup. Cadence says the routine runs only on a manual kickoff: no schedule
   * starts one. */
  protected recordRows(panel: RoutinePanelVm, graph: TemplateRef<unknown>, runs: TemplateRef<unknown>): readonly KitFact[] {
    return [
      { label: 'Graph', template: graph, testid: 'gardening-routine-fact-graph' },
      { label: 'Default scope', value: panel.record.defaultScopeSlug, testid: 'gardening-routine-fact-scope' },
      { label: 'Model · effort', value: panel.record.modelEffort, testid: 'gardening-routine-fact-model' },
      { label: 'Cadence', value: 'manual — runs only when kicked off', testid: 'gardening-routine-fact-cadence' },
      { label: 'Runs', template: runs, testid: 'gardening-routine-fact-runs' },
    ];
  }

  protected readonly compactRef = compactRef;

  /** The trend's four counts as an aligned fact grid — `recordRows`' own shape. */
  protected trendRows(trend: TrendSummaryVm): readonly KitFact[] {
    return [
      { label: 'created', value: String(trend.created) },
      { label: 'outflow', value: String(trend.outflow) },
      { label: 'withdrawn', value: String(trend.withdrawn) },
      { label: 'reopened', value: String(trend.reopened) },
    ];
  }

  /** Open a confirmation before emitting `retire` once the operator confirms. */
  protected onRetire(): void {
    const name = this.vm()?.record.name;
    if (name === undefined) return;
    this.pendingConfirm.set({
      heading: `Retire routine ${name}`,
      message: `Retire routine ${name}? It stops offering a run; its runs, findings, proposals, and closures stay live, queryable, and attributable.`,
      confirmLabel: 'Retire',
      variant: 'danger',
      run: () => this.retire.emit(name),
    });
  }

  /** Open a confirmation before emitting `enable` once the operator confirms. */
  protected onEnable(): void {
    const name = this.vm()?.record.name;
    if (name === undefined) return;
    this.pendingConfirm.set({
      heading: `Re-enable routine ${name}`,
      message: `Re-enable routine ${name}? It resumes offering a run.`,
      confirmLabel: 'Re-enable',
      variant: 'primary',
      run: () => this.enable.emit(name),
    });
  }

  protected onConfirmed(): void {
    const pending = this.pendingConfirm();
    this.pendingConfirm.set(null);
    pending?.run();
  }

  protected onCancelled(): void {
    this.pendingConfirm.set(null);
  }
}
