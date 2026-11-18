import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { KitCheckbox, KitDialog, KitField, KitForm, KitNumberInput, KitOrDefault, KitSelect, KitTextInput } from 'fleet';
import type { FieldChangeView } from 'fleet';

import { ConfigFieldDiff } from './config-field-diff';
import {
  type ConfigFieldTreeMode,
  type ConfigFieldView,
  defaultChoiceOf,
  dependentsOf,
  fieldChanges,
  optionsOf,
  shownFieldViews,
} from './config-field-tree.model';
import { ConfigMarkInput } from './config-mark-input';
import { ProjectColorPicker } from './projects/project-color-picker';

/**
 * The typed config dialog: a record type's Signal Forms `FieldTree<D>` drawn from presentation-only
 * {@link ConfigFieldView}s, inside the kit's form chrome. Presentational: the container owns the draft, the rules, and
 * the submit — this part emits `save` for it to answer with `submitForm`, and `closed` when the person leaves.
 */
@Component({
  selector: 'app-config-field-tree-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ConfigFieldDiff,
    ConfigMarkInput,
    FormField,
    KitCheckbox,
    KitDialog,
    KitField,
    KitForm,
    KitNumberInput,
    KitOrDefault,
    KitSelect,
    KitTextInput,
    ProjectColorPicker,
  ],
  templateUrl: './config-field-tree-dialog.html',
  styleUrl: './config-field-tree-dialog.css',
})
export class ConfigFieldTreeDialog<D> {
  readonly heading = input.required<string>();
  readonly mode = input.required<ConfigFieldTreeMode>();
  readonly form = input.required<FieldTree<D>>();
  readonly fields = input.required<readonly ConfigFieldView<NoInfer<D>>[]>();
  /** The draft an edit started from — the container's captured baseline; `null` for a create or replace. */
  readonly baseline = input<NoInfer<D> | null>(null);
  readonly submitLabel = input.required<string>();
  /** A standing note under the fields — what a value means or what changing it does. */
  readonly hint = input<string | null>(null);
  readonly testidPrefix = input.required<string>();

  readonly save = output<void>();
  readonly closed = output<void>();

  /** The keys of the mark fields whose file is being read. */
  private readonly reading = signal<ReadonlySet<string>>(new Set());

  protected readonly submitting = computed(() => this.form()().submitting());
  protected readonly draft = computed(() => this.form()().value());
  protected readonly visibleFields = computed(() => shownFieldViews(this.fields(), this.mode(), this.draft()));

  protected readonly changes = computed<readonly FieldChangeView[]>(() => {
    const baseline = this.baseline();
    return this.mode() === 'edit' && baseline !== null ? fieldChanges(this.fields(), baseline, this.draft()) : [];
  });

  /** Whether a shown mark's file is still being read; a mark that left the form with its read in flight no longer holds Save. */
  private readonly readingShown = computed(() => this.visibleFields().some((field) => this.reading().has(field.key)));

  /** Save is held while an edit has nothing to send, and while a mark's file is still being read. */
  protected readonly saveHeld = computed(
    () => this.readingShown() || (this.mode() === 'edit' && this.changes().length === 0),
  );

  protected textField(key: string): FieldTree<string> {
    return (this.form() as unknown as Record<string, FieldTree<string>>)[key];
  }

  protected flagField(key: string): FieldTree<boolean> {
    return (this.form() as unknown as Record<string, FieldTree<boolean>>)[key];
  }

  protected defaultChoiceFor(field: ConfigFieldView<D>) {
    return defaultChoiceOf(field);
  }

  protected optionsFor(field: ConfigFieldView<D>) {
    return optionsOf(field, this.draft());
  }

  protected colorChoices(field: ConfigFieldView<D>): readonly string[] {
    return (field.options ?? []).map((option) => option.value);
  }

  protected onColor(field: ConfigFieldView<D>, color: string): void {
    const state = this.textField(field.key)();
    state.value.set(color);
    state.markAsTouched();
    this.onChanged(field.key);
  }

  /** A field changed: every field whose choices hang on it, directly or through another, is cleared. */
  protected onChanged(key: string): void {
    for (const dependent of dependentsOf(this.fields(), key)) this.textField(dependent)().value.set('');
  }

  protected onReading(key: string, reading: boolean): void {
    this.reading.update((keys) => {
      if (keys.has(key) === reading) return keys;
      const next = new Set(keys);
      if (reading) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  /** Escape, a backdrop click, and Cancel all close — except while the save is in flight. */
  protected onClosed(): void {
    if (this.submitting()) return;
    this.reading.set(new Set());
    this.closed.emit();
  }
}
