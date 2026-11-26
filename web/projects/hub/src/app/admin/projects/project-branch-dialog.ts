import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { KitButton, KitErrorLine, KitField, KitTextInput } from 'fleet/kit';
import { KitDialog } from 'fleet/kit/dialog';

import type { LinkBranchVm } from './projects.model';

/** Presentational branch editor. Saving and explicitly inheriting are separate container actions. */
@Component({
  selector: 'app-project-branch-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitButton, KitDialog, KitErrorLine, KitField, KitTextInput],
  templateUrl: './project-branch-dialog.html',
  styleUrl: './project-branch-dialog.css',
})
export class ProjectBranchDialog {
  readonly heading = input.required<string>();
  /** The captured branch at opening, not a live read. */
  readonly branch = input.required<LinkBranchVm>();
  readonly form = input.required<FieldTree<unknown>>();
  readonly branchField = input.required<FieldTree<string>>();
  readonly testidPrefix = input.required<string>();

  readonly closed = output<void>();
  readonly submitted = output<void>();
  readonly inherit = output<void>();

  protected readonly submitting = computed(() => this.form()().submitting());
  protected readonly messages = computed(() => {
    const state = this.form()();
    return state.touched() ? state.errors().map((error) => error.message || (error.kind === 'required' ? 'Required' : 'Invalid')) : [];
  });
  protected readonly canSave = computed(() => {
    const value = this.branchField()().value();
    return !this.submitting() && value !== '' && value !== this.branch().override;
  });
  protected readonly canInherit = computed(() => !this.submitting() && this.branch().override !== null);

  protected onSave(): void {
    if (this.canSave()) this.submitted.emit();
  }

  protected onInherit(): void {
    if (this.canInherit()) this.inherit.emit();
  }

  protected onClosed(): void {
    if (!this.submitting()) this.closed.emit();
  }
}
