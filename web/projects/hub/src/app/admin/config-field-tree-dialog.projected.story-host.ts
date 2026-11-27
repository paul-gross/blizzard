import { Component, computed, input, signal } from '@angular/core';
import { form, FormField } from '@angular/forms/signals';
import { submitForm } from 'fleet/forms';
import { KitField, KitTextInput } from 'fleet/kit';

import { fieldChanges, type ConfigFieldView } from './config-field-tree.model';
import { ConfigFieldTreeDialog } from './config-field-tree-dialog';

interface ProjectedDraft {
  kind: string;
  project: string;
  setting: { value: string };
}

@Component({
  selector: 'app-story-projected-dialog',
  imports: [ConfigFieldTreeDialog, FormField, KitField, KitTextInput],
  templateUrl: './config-field-tree-dialog.projected.form-story.html',
})
export class ProjectedDialogHost {
  readonly initialChanges = input(0);
  readonly baseline: ProjectedDraft = { kind: 'forge', project: 'blizzard', setting: { value: 'before' } };
  readonly fields: readonly ConfigFieldView<ProjectedDraft>[] = [
    { key: 'kind', label: 'Kind', kind: 'select', options: [{ value: 'forge', label: 'Forge' }, { value: 'tracker', label: 'Tracker' }] },
    { key: 'project', label: 'Project', kind: 'select', filterBy: 'kind', options: [
      { value: 'blizzard', label: 'Blizzard', kind: 'forge' }, { value: 'BZ', label: 'BZ board', kind: 'tracker' },
    ] },
  ];
  readonly f = form(signal<ProjectedDraft>({ ...this.baseline, setting: { ...this.baseline.setting } }));
  readonly lastChanged = signal('none');
  readonly hint = computed(() => `Last dialog field changed: ${this.lastChanged()}. Changing Kind clears Project and Setting.`);
  readonly diff = computed(() => {
    const draft = this.f().value();
    const changes = [...fieldChanges(this.fields, this.baseline, draft)];
    if (draft.setting.value !== this.baseline.setting.value) {
      changes.push({ field: 'Setting', old: this.baseline.setting.value, new: draft.setting.value || null, old_label: null, new_label: null });
    }
    return changes;
  });
  saved: ProjectedDraft[] = [];

  constructor() {
    queueMicrotask(() => {
      if (this.initialChanges() > 0) this.f.setting.value().value.set('after');
      if (this.initialChanges() > 1) this.f.project().value.set('');
    });
  }

  onFieldChanged(key: string): void {
    this.lastChanged.set(key);
    if (key === 'kind') {
      this.f.project().value.set('');
      this.f.setting.value().value.set('');
    }
  }

  async onSave(): Promise<void> {
    await submitForm(this.f, async (draft) => {
      this.saved.push(draft);
      return { ok: true };
    });
  }
}
