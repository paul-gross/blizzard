import { Component, input, output, signal } from '@angular/core';
import { type FieldTree, form, submit } from '@angular/forms/signals';

import { blankRecordDraft, type HostRecordKind, type RecordDraft, type RecordFormMode, recordFields } from './host-records.domain';
import { HostRecordFormDialog } from './host-record-form-dialog';

/** The dialog over a draft of its own: `form()` needs an injection context. `draft` seeds the fields, `fieldErrors`
 * refuses fields beside them, `formError` puts a line on the dialog, and `submitting` holds a save in flight. */
@Component({
  selector: 'app-story-host-record-form-dialog',
  imports: [HostRecordFormDialog],
  templateUrl: './host-record-form-dialog.form-story.html',
})
export class HostRecordFormDialogHost {
  readonly kind = input<HostRecordKind>('workspace');
  readonly mode = input<RecordFormMode>('create');
  readonly heading = input('Create workspace');
  readonly draft = input<RecordDraft>({});
  readonly fieldErrors = input<Readonly<Record<string, string>>>({});
  readonly formError = input<string | null>(null);
  readonly submitting = input(false);

  readonly save = output<void>();
  readonly closed = output<void>();

  private readonly model = signal<RecordDraft>(blankRecordDraft());
  readonly f = form(this.model);

  protected fields() {
    return recordFields(this.kind(), this.mode());
  }

  constructor() {
    queueMicrotask(() => this.seed());
  }

  private seed(): void {
    this.model.update((draft) => ({ ...draft, ...this.draft() }));
    const children = this.f as unknown as Record<string, FieldTree<string>>;
    const fieldErrors = Object.entries(this.fieldErrors());
    const line = this.formError();
    if (this.submitting()) {
      void submit(this.f, () => new Promise<undefined>(() => undefined));
    } else if (fieldErrors.length > 0 || line !== null) {
      this.f().markAsTouched();
      for (const [name] of fieldErrors) children[name]().markAsTouched();
      void submit(this.f, async () => [
        ...fieldErrors.map(([name, message]) => ({ kind: 'hub', fieldTree: children[name], message })),
        ...(line === null ? [] : [{ kind: 'hub', fieldTree: this.f, message: line }]),
      ]);
    }
  }
}
