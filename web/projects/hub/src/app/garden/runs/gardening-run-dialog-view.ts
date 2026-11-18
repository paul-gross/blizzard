import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { RouterLink } from '@angular/router';

import {
  FleetWhen,
  KitAsyncState,
  KitButton,
  KitDialog,
  KitErrorLine,
  KitField,
  KitOption,
  KitOptionGroup,
  KitSelect,
  KitTextInput,
  type KitAsyncStateValue,
  type RoutineBaselineView,
  type RoutineRunResponse,
  type ScopeView,
  type Urgency,
} from 'fleet';
import { AppLink } from 'fleet/shell';

import type { RunDraft } from '../core/run.domain';
import { urgencyOptions } from '../../core/urgency-options.model';
import { GardeningRunScopeField } from './gardening-run-scope-field';

/**
 * The gardening run dialog's presentational view — scope, mode, urgency, and charge note, the delta baseline display, the
 * run action, and the post-run confirmation, all over inputs and outputs only. No query or client dependency: the
 * container injects the scope reads and the run service, owns the draft and its rules, and maps their state into
 * `form()`/`state()`/`submitting()`/`confirmedRun()` (`bzh:frontend-container-presentational`).
 *
 * Renders the field tree it is handed; a hub refusal arrives as errors on that tree — on a field, or on the root, which
 * the footer shows.
 */
@Component({
  selector: 'app-gardening-run-dialog-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    FleetWhen,
    FormField,
    GardeningRunScopeField,
    KitAsyncState,
    KitButton,
    KitDialog,
    KitErrorLine,
    KitField,
    KitOption,
    KitOptionGroup,
    KitSelect,
    KitTextInput,
    RouterLink,
    AppLink,
  ],
  templateUrl: './gardening-run-dialog-view.html',
  styleUrl: './gardening-run-dialog-view.css',
})
export class GardeningRunDialogView {
  readonly routineName = input.required<string>();
  readonly form = input.required<FieldTree<RunDraft>>();
  readonly urgencies = input<readonly Urgency[]>([]);
  /** The urgency a run of the routine mints at when it names none; `null` when the hub could not resolve one. */
  readonly effectiveUrgency = input<{ id: string; slug: string } | null>(null);

  /** Every non-retired scope, previously-swept-by-this-routine first;
   * rendered in the order given. */
  readonly scopes = input.required<readonly ScopeView[]>();

  readonly sweptSlugs = input.required<ReadonlySet<string>>();

  /** The delta baseline for the chosen scope, or `undefined` for a never-swept pair or no choice yet — the one fact
   * the baseline display and the delta option's availability rest on. */
  readonly baseline = input<RoutineBaselineView | undefined>(undefined);

  /** The scope/baseline reads' combined async state — gates the form body. */
  readonly state = input.required<KitAsyncStateValue>();

  readonly submitting = input(false);

  /** The completed run, once submitted successfully — flips the dialog from the form
   * to the confirmation. */
  readonly confirmedRun = input<RoutineRunResponse | null>(null);

  readonly closed = output<void>();

  /** Run pressed. */
  readonly save = output<void>();

  protected readonly deltaAvailable = computed(() => this.baseline() !== undefined);

  protected readonly urgencyOptions = computed(() => {
    const ref = this.effectiveUrgency();
    const name = ref === null ? null : (this.urgencies().find((u) => u.id === ref.id)?.name ?? ref.slug);
    return urgencyOptions(this.urgencies(), { key: 'slug', retired: 'exclude', inherit: { label: 'Routine default', name } });
  });

  protected readonly canSubmit = computed(() => !this.submitting() && this.state() === 'ready');

  /** What the hub refused the run with that no field owns. */
  protected readonly formErrors = computed(() =>
    this.form()()
      .errors()
      .map((error) => error.message || 'Run failed.'),
  );

  protected onSaveClick(): void {
    if (this.canSubmit()) this.save.emit();
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
