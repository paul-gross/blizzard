import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { KitButton, KitDialog, KitTextInput } from 'fleet';

import { ConfigFieldDiff } from './config-field-diff';
import {
  type ConfigFieldDef,
  type ConfigFormValues,
  configEdit,
  editableFields,
  emptyFormValues,
  missingRequired,
} from './config-edit.model';

/** What the dialog is for: a create takes every field; an edit takes the editable ones
 * and shows a "Will change" diff before Save; a replace takes whatever fields it is
 * given — a secret's value alone. */
export type ConfigFormMode = 'create' | 'edit' | 'replace';

/**
 * The form dialog every config write shares — fields from {@link ConfigFieldDef}s, a
 * "Will change" diff in edit mode, and the submit error from the write that failed,
 * shown inline with the dialog left open. Presentational: it owns the field values as
 * a local signal and emits them; the container owns the mutation. Mounted with `@if`
 * around its open state, so a fresh instance — and an emptied password field — exists
 * for every open.
 */
@Component({
  selector: 'app-config-form-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldDiff, KitButton, KitDialog, KitTextInput],
  templateUrl: './config-form-dialog.html',
  styleUrl: './config-form-dialog.css',
})
export class ConfigFormDialog {
  readonly heading = input.required<string>();
  readonly mode = input.required<ConfigFormMode>();
  readonly fields = input.required<readonly ConfigFieldDef[]>();
  /** The shown record's values for an edit; empty for a create or replace. */
  readonly initial = input<ConfigFormValues | null>(null);
  readonly submitLabel = input.required<string>();
  readonly submitting = input(false);
  readonly submitError = input<string | null>(null);
  readonly testidPrefix = input.required<string>();

  readonly closed = output<void>();
  readonly submitted = output<ConfigFormValues>();

  private readonly typed = signal<ConfigFormValues | null>(null);

  protected readonly shown = computed(() => this.initial() ?? emptyFormValues(this.fields()));
  protected readonly values = computed(() => this.typed() ?? this.shown());
  protected readonly visibleFields = computed(() =>
    this.mode() === 'edit' ? editableFields(this.fields()) : this.fields(),
  );
  protected readonly edit = computed(() => configEdit(this.fields(), this.shown(), this.values()));

  protected readonly canSubmit = computed(() => {
    if (this.submitting()) return false;
    if (this.mode() === 'edit') return this.edit().diff.length > 0;
    return missingRequired(this.visibleFields(), this.values()).length === 0;
  });

  protected textOf(key: string): string {
    const value = this.values()[key];
    return typeof value === 'string' ? value : '';
  }

  protected flagOf(key: string): boolean {
    return this.values()[key] === true;
  }

  protected onText(key: string, value: string): void {
    this.typed.set({ ...this.values(), [key]: value });
  }

  protected onFlag(key: string, event: Event): void {
    this.typed.set({
      ...this.values(),
      [key]: (event.target as HTMLInputElement).checked,
    });
  }

  protected onSubmit(): void {
    if (this.canSubmit()) this.submitted.emit(this.values());
  }

  /** Escape, a backdrop click, and Cancel all close — except while a write is in
   * flight. Closing drops the typed values, a password with them. */
  protected onClosed(): void {
    if (this.submitting()) return;
    this.typed.set(null);
    this.closed.emit();
  }
}
