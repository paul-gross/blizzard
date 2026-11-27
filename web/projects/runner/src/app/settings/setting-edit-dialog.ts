import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { KitBadge, KitButton, KitErrorLine, KitField, KitTextInput } from 'fleet/kit';
import { KitDialog } from 'fleet/kit/dialog';

import type { SettingDraft } from './settings.domain';
import type { SettingBadge } from './settings.model';

/** A one-key edit of a settings record: the key's value as typed, Save, Reset to default, and Cancel. A refusal of
 * the key renders beside its value; one the key does not own — a revision conflict — on the dialog's own line.
 * Presentational: its container owns the form, the baseline, and the write. */
@Component({
  selector: 'app-setting-edit-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitBadge, KitButton, KitDialog, KitErrorLine, KitField, KitTextInput],
  templateUrl: './setting-edit-dialog.html',
  styleUrl: './setting-edit-dialog.css',
})
export class SettingEditDialog {
  readonly form = input.required<FieldTree<SettingDraft>>();
  readonly recordTitle = input.required<string>();
  readonly settingKey = input.required<string>();
  readonly lifetime = input.required<SettingBadge>();
  /** Whether the key holds a written entry Reset to default would remove. */
  readonly canReset = input(true);

  readonly save = output<void>();
  readonly resetToDefault = output<void>();
  readonly closed = output<void>();

  protected readonly submitting = computed(() => this.form()().submitting());

  /** The form's own refusals, once it has been submitted. */
  protected readonly messages = computed(() => {
    const state = this.form()();
    return state.touched() ? state.errors().map((error) => error.message ?? '').filter((m) => m !== '') : [];
  });

  protected onSave(): void {
    if (!this.submitting()) this.save.emit();
  }

  protected onClosed(): void {
    if (!this.submitting()) this.closed.emit();
  }
}
