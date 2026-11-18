import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { KitButton, KitDialog, KitErrorLine, KitField, KitSelect } from 'fleet';

import type { LinkChoiceVm } from './projects.model';

/** Presentational link chooser; its container owns the form, initial choice and submission. */
@Component({
  selector: 'app-project-link-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitButton, KitDialog, KitErrorLine, KitField, KitSelect],
  templateUrl: './project-link-dialog.html',
  styleUrl: './project-link-dialog.css',
})
export class ProjectLinkDialog {
  readonly heading = input.required<string>();
  readonly fieldLabel = input.required<string>();
  readonly choices = input.required<readonly LinkChoiceVm[]>();
  readonly note = input.required<string>();
  readonly noneText = input.required<string>();
  readonly form = input.required<FieldTree<unknown>>();
  readonly choiceField = input.required<FieldTree<string>>();
  readonly testidPrefix = input.required<string>();

  readonly closed = output<void>();
  readonly submitted = output<void>();

  protected readonly submitting = computed(() => this.form()().submitting());
  protected readonly messages = computed(() => {
    const state = this.form()();
    return state.touched() ? state.errors().map((error) => error.message || (error.kind === 'required' ? 'Required' : 'Invalid')) : [];
  });
  protected readonly canSubmit = computed(() => !this.submitting() && this.choices().length > 0 && this.choiceField()().value() !== '');

  protected onSubmit(): void {
    if (this.canSubmit()) this.submitted.emit();
  }

  protected onClosed(): void {
    if (!this.submitting()) this.closed.emit();
  }
}
