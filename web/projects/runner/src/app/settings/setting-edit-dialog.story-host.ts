import { Component, input, output, signal } from '@angular/core';
import { form, submit } from '@angular/forms/signals';

import type { SettingDraft } from './settings.domain';
import type { SettingBadge } from './settings.model';
import { SettingEditDialog } from './setting-edit-dialog';

/** The dialog over a draft of its own: `form()` needs an injection context. `fieldError` refuses the value beside it,
 * `formError` puts a line on the dialog, and `submitting` holds a save in flight. */
@Component({
  selector: 'app-story-setting-edit-dialog',
  imports: [SettingEditDialog],
  templateUrl: './setting-edit-dialog.form-story.html',
})
export class SettingEditDialogHost {
  readonly value = input('10');
  readonly recordTitle = input('r-claude');
  readonly settingKey = input('throttle.budget.usd');
  readonly lifetime = input<SettingBadge>({ label: 'pass', tone: 'running' });
  readonly canReset = input(true);
  readonly fieldError = input<string | null>(null);
  readonly formError = input<string | null>(null);
  readonly submitting = input(false);

  readonly save = output<void>();
  readonly resetToDefault = output<void>();
  readonly closed = output<void>();

  readonly f = form(signal<SettingDraft>({ value: '' }));

  constructor() {
    queueMicrotask(() => this.seed());
  }

  private seed(): void {
    this.f.value().value.set(this.value());
    const field = this.fieldError();
    const line = this.formError();
    if (this.submitting()) {
      void submit(this.f, () => new Promise<undefined>(() => undefined));
    } else if (field !== null || line !== null) {
      this.f().markAsTouched();
      this.f.value().markAsTouched();
      void submit(this.f, async () => [
        ...(field === null ? [] : [{ kind: 'hub', fieldTree: this.f.value, message: field }]),
        ...(line === null ? [] : [{ kind: 'hub', fieldTree: this.f, message: line }]),
      ]);
    }
  }
}
