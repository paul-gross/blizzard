import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { KitButton, KitErrorLine, KitField, KitTextInput } from 'fleet/kit';
import { KitDialog } from 'fleet/kit/dialog';

import type { RecordDraft, RecordField } from './host-records.domain';

/** A create or an edit of a workspace or subscription record: one field per key its request takes, Save, and
 * Cancel. A refusal of a key renders beside its field; one no field owns — a revision conflict — on the dialog's own
 * line. Presentational: its container owns the form, the baseline, and the write. */
@Component({
  selector: 'app-host-record-form-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitButton, KitDialog, KitErrorLine, KitField, KitTextInput],
  templateUrl: './host-record-form-dialog.html',
  styleUrl: './host-record-form-dialog.css',
})
export class HostRecordFormDialog {
  readonly form = input.required<FieldTree<RecordDraft>>();
  readonly heading = input.required<string>();
  readonly fields = input.required<readonly RecordField[]>();

  readonly save = output<void>();
  readonly closed = output<void>();

  protected readonly submitting = computed(() => this.form()().submitting());

  /** The form's own refusals, once it has been submitted. */
  protected readonly messages = computed(() => {
    const state = this.form()();
    return state.touched() ? state.errors().map((error) => error.message ?? '').filter((m) => m !== '') : [];
  });

  protected fieldOf(name: string): FieldTree<string> {
    return (this.form() as unknown as Record<string, FieldTree<string>>)[name];
  }

  protected onSave(): void {
    if (!this.submitting()) this.save.emit();
  }

  protected onClosed(): void {
    if (!this.submitting()) this.closed.emit();
  }
}
