import { Component, input, signal } from '@angular/core';
import { form, required, schema, validate } from '@angular/forms/signals';
import { type SaveOutcome, submitForm } from 'fleet';

import { applyShownWhen } from './config-field-tree.form';
import type { ConfigFieldView } from './config-field-tree.model';
import { ConfigFieldTreeDialog } from './config-field-tree-dialog';

/** A create draft with a dependent choice and a conditional required field. */
export interface SourceDraft {
  name: string;
  kind: string;
  project: string;
  token: string;
}

export const SOURCE_FIELDS: readonly ConfigFieldView<SourceDraft>[] = [
  { key: 'name', label: 'Name', kind: 'text', createOnly: true },
  {
    key: 'kind',
    label: 'Kind',
    kind: 'select',
    options: [
      { value: 'forge', label: 'Forge' },
      { value: 'tracker', label: 'Tracker' },
    ],
  },
  {
    key: 'project',
    label: 'Project',
    kind: 'select',
    filterBy: 'kind',
    options: [
      { value: 'blizzard', label: 'Blizzard', kind: 'forge' },
      { value: 'winter', label: 'Winter', kind: 'forge' },
      { value: 'BZ', label: 'BZ board', kind: 'tracker' },
    ],
  },
  { key: 'token', label: 'Token', kind: 'password', shownWhen: { key: 'kind', value: 'tracker' } },
];

const sourceRules = schema<SourceDraft>((p) => {
  applyShownWhen(p, SOURCE_FIELDS);
  required(p.name);
  required(p.kind);
  required(p.token);
});

/** An edit draft with text, number, secret, checkbox, color and mark fields. */
export interface UrgencyDraft {
  label: string;
  rank: string;
  note: string;
  active: boolean;
  color: string;
  mark: string;
}

export const URGENCY_FIELDS: readonly ConfigFieldView<UrgencyDraft>[] = [
  { key: 'label', label: 'Label', kind: 'text' },
  { key: 'rank', label: 'Rank', kind: 'number' },
  { key: 'note', label: 'Note', kind: 'secret-text' },
  { key: 'active', label: 'Active', kind: 'checkbox' },
  {
    key: 'color',
    label: 'Color',
    kind: 'color',
    options: [
      { value: 'red', label: 'Red' },
      { value: 'blue', label: 'Blue' },
    ],
  },
  { key: 'mark', label: 'Mark', kind: 'mark' },
];

const urgencyRules = schema<UrgencyDraft>((p) => {
  required(p.label);
  validate(p.rank, ({ value }) => (/^\d*$/.test(value().trim()) ? null : { kind: 'rank', message: 'Enter a whole number' }));
});

export const URGENCY_BASELINE: UrgencyDraft = { label: 'Urgent', rank: '3', note: '', active: false, color: 'red', mark: '' };

/** A container for the create dialog: it owns the draft, the rules and the submit. */
@Component({
  selector: 'app-story-source-dialog',
  imports: [ConfigFieldTreeDialog],
  templateUrl: './config-field-tree-dialog.source.form-story.html',
})
export class SourceDialogHost {
  /** What the stand-in save answers. */
  readonly outcome = input<SaveOutcome<SourceDraft>>({ ok: true });
  readonly kind = input('');
  readonly fields = SOURCE_FIELDS;
  readonly f = form(signal<SourceDraft>({ name: '', kind: '', project: '', token: '' }), sourceRules);
  saved: SourceDraft[] = [];
  closes = 0;

  constructor() {
    queueMicrotask(() => {
      if (this.kind()) this.f.kind().value.set(this.kind());
    });
  }

  async onSave(): Promise<void> {
    await submitForm(this.f, async (draft) => {
      const outcome = this.outcome();
      if (outcome.ok) this.saved.push(draft);
      return outcome;
    });
  }
}

/** A container for the edit dialog: a baseline captured once, and a draft apart from it. */
@Component({
  selector: 'app-story-urgency-dialog',
  imports: [ConfigFieldTreeDialog],
  templateUrl: './config-field-tree-dialog.urgency.form-story.html',
})
export class UrgencyDialogHost {
  readonly outcome = input<SaveOutcome<UrgencyDraft>>({ ok: true });
  readonly fields = URGENCY_FIELDS;
  readonly baseline = URGENCY_BASELINE;
  readonly f = form(signal<UrgencyDraft>({ ...URGENCY_BASELINE }), urgencyRules);
  saved: UrgencyDraft[] = [];
  closes = 0;

  async onSave(): Promise<void> {
    await submitForm(this.f, async (draft) => {
      const outcome = this.outcome();
      if (outcome.ok) this.saved.push(draft);
      return outcome;
    });
  }
}
