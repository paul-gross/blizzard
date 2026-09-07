import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import {
  FleetWhen,
  KitAsyncState,
  KitButton,
  KitDialog,
  KitOption,
  KitPanel,
  KitTextInput,
  type KitAsyncStateValue,
  type RoutineBaselineView,
  type RoutineRunResponse,
  type ScopeView,
} from 'fleet';

import { EMPTY_SCOPE_SELECTION, GardeningRunScopeField, type ScopeSelection } from './gardening-run-scope-field';

/** What the view asks the container to do once the operator submits. */
export interface RunSubmission {
  readonly selection: ScopeSelection;
  readonly mode: 'full' | 'delta';
  readonly note: string | null;
}

/**
 * The gardening run dialog's presentational view (blizzard#399 D6) — three fields
 * (scope, mode, charge note) and nothing else, the delta baseline display, the run
 * submission, and the post-run confirmation, all over inputs and outputs only. No query
 * or client dependency: the container injects the scope reads and the run mutation and
 * maps their async state into `state()`/`submitting()`/`submitError()`/`confirmedRun()`
 * (`bzh:frontend-container-presentational`).
 *
 * Owns every field's live value as local signals — `scopeSelection`/`mode`/`note` —
 * since the host page renders this component (and its container) with `@if`, tearing
 * it down between runs (the routine panel's own `run` output), so a stale value never
 * survives to a later open the way a container-held signal would need an explicit
 * reset to avoid.
 */
@Component({
  selector: 'app-gardening-run-dialog-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    FleetWhen,
    GardeningRunScopeField,
    KitAsyncState,
    KitButton,
    KitDialog,
    KitOption,
    KitPanel,
    KitTextInput,
    RouterLink,
  ],
  templateUrl: './gardening-run-dialog-view.html',
  styleUrl: './gardening-run-dialog-view.css',
})
export class GardeningRunDialogView {
  readonly routineName = input.required<string>();

  /** Every non-retired scope, previously-swept-by-this-routine first (D5) — the
   * container's own ordering. */
  readonly scopes = input.required<readonly ScopeView[]>();

  readonly sweptSlugs = input.required<ReadonlySet<string>>();

  /** Every scope this routine has swept, D5's own read — looked up by the currently
   * selected scope to resolve the delta baseline display and the delta-steering rule. */
  readonly baselines = input.required<readonly RoutineBaselineView[]>();

  /** The scope/baseline reads' combined async state — gates the form body. */
  readonly state = input.required<KitAsyncStateValue>();

  readonly submitting = input(false);

  /** Set on a failed scope create or run (D3's surfaced refusal); `null` between
   * attempts. */
  readonly submitError = input<string | null>(null);

  /** The completed run, once submitted successfully — flips the dialog from the form
   * to the confirmation (D6). */
  readonly confirmedRun = input<RoutineRunResponse | null>(null);

  readonly closed = output<void>();

  readonly runSubmitted = output<RunSubmission>();

  protected readonly scopeSelection = signal<ScopeSelection>(EMPTY_SCOPE_SELECTION);
  protected readonly mode = signal<'full' | 'delta'>('full');
  protected readonly note = signal('');

  /** The delta baseline for the currently selected scope, or `undefined` for a
   * never-swept pair or no selection yet — D5's own read is the one fact both this
   * display and {@link deltaAvailable} rest on. */
  protected readonly selectedBaseline = computed<RoutineBaselineView | undefined>(() => {
    const sel = this.scopeSelection();
    if (!sel) return undefined;
    return this.baselines().find((b) => b.scope_slug === sel);
  });

  protected readonly deltaAvailable = computed(() => this.selectedBaseline() !== undefined);

  protected readonly canSubmit = computed(() => {
    if (this.submitting()) return false;
    return this.scopeSelection().length > 0;
  });

  constructor() {
    // Defaults the picker to the first (previously-swept-first-ordered) scope the
    // instant the read resolves — a bare empty selection otherwise leaves nothing
    // checked until the operator acts.
    effect(() => {
      const scopes = this.scopes();
      if (scopes.length > 0 && !this.scopeSelection()) {
        this.scopeSelection.set(scopes[0].slug);
      }
    });
    // The delta-steering rule (D5): a scope that stops carrying a baseline — the
    // operator switched to a never-swept or new one — steers back to full rather than
    // leaving delta selected with nothing to run it against.
    effect(() => {
      if (!this.deltaAvailable() && this.mode() === 'delta') this.mode.set('full');
    });
  }

  protected onSubmitClick(): void {
    if (!this.canSubmit()) return;
    this.runSubmitted.emit({ selection: this.scopeSelection(), mode: this.mode(), note: this.note().trim() || null });
  }

  /** Escape, a backdrop click, and Cancel all route through `KitDialog`'s one
   * `(closed)` output — gating it here, rather than each dismissal path separately,
   * keeps a run in flight from being torn down before its confirmed chunk id and
   * board link ever render. The run itself still lands; only the confirmation would
   * be lost. */
  protected onClosed(): void {
    if (this.submitting()) return;
    this.closed.emit();
  }
}
