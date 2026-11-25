import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';
import { asyncState, createRefusalState, type EditBaseline, submitForm } from 'fleet';

import type { SettingDraft } from './settings.domain';
import { settingEditRules } from './settings.form';
import {
  inForceLine,
  lifetimeBadge,
  type SettingBadge,
  settingGroups,
  settingRowOf,
  type SettingsRecordRef,
  whileEditing,
} from './settings.model';
import { injectSettingsQuery } from './settings.query';
import { injectSettingsService, type SettingEditKey } from './settings.service';
import { toSettingBaseline } from './settings.translate';
import { SettingEditDialog } from './setting-edit-dialog';
import { SettingsRecordView } from './settings-record-view';

/** The key an open edit writes, as its dialog shows it. */
interface OpenEdit extends SettingEditKey {
  readonly lifetime: SettingBadge;
  readonly canReset: boolean;
}

/**
 * One settings record — the host's or a hosted runner's — read live, with its one-key edit. Edit captures the
 * baseline from the record's read, and Save or Reset to default writes against it, so a refresh of the read changes
 * neither; a refused write keeps both, and Cancel ends the edit.
 */
@Component({
  selector: 'app-settings-record',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [SettingEditDialog, SettingsRecordView],
  templateUrl: './settings-record.html',
})
export class SettingsRecordPanel {
  readonly ref = input.required<SettingsRecordRef>();
  /** Whether the session is the host's superuser, who alone edits. */
  readonly superuser = input.required<boolean>();

  private readonly query = injectSettingsQuery(
    () => this.ref().target,
    () => this.ref().unserved === null,
  );
  private readonly service = injectSettingsService(() => this.ref().target);

  protected readonly record = computed(() => this.query.data());
  protected readonly state = computed(() =>
    asyncState(this.query, (this.record()?.settings.length ?? 0) === 0 && (this.record()?.settingsError ?? null) === null),
  );
  protected readonly line = computed(() => inForceLine(this.record()));
  protected readonly groups = computed(() => settingGroups(this.record(), this.superuser(), this.ref().retired));
  protected readonly pending = computed(() => this.service.pending());

  private readonly baseline = signal<EditBaseline<SettingDraft> | null>(null);
  protected readonly editing = signal<OpenEdit | null>(null);
  private readonly draft = signal<SettingDraft>({ value: '' });
  private readonly refusal = createRefusalState<SettingDraft>();
  private readonly tree = form(this.draft, (p) => {
    apply(p, this.refusal.schema);
    apply(p, settingEditRules(() => this.editing()?.current));
  });

  protected readonly editForm = computed(() => whileEditing(this.baseline(), this.tree));

  protected onEdit(key: string): void {
    const record = this.record();
    const row = settingRowOf(record, key);
    if (record === undefined || row === null) return;
    const baseline = toSettingBaseline(this.ref().id, record, row);
    this.editing.set({ key, current: row.value, lifetime: lifetimeBadge(row.lifetime), canReset: row.source === 'entry' });
    this.refusal.clear();
    this.tree().reset({ ...baseline.draft });
    this.draft.set({ ...baseline.draft });
    this.baseline.set(baseline);
  }

  protected async onSave(): Promise<void> {
    const baseline = this.baseline();
    const edit = this.editing();
    if (baseline === null || edit === null) return;
    const saved = await submitForm(this.tree, (draft) => this.service.set(edit, baseline, draft), {
      refusalState: this.refusal,
      isCurrent: () => this.baseline() === baseline,
    });
    if (saved) this.onClose();
  }

  protected async onReset(): Promise<void> {
    const baseline = this.baseline();
    const edit = this.editing();
    if (baseline === null || edit === null) return;
    const saved = await submitForm(this.tree, () => this.service.unset(edit, baseline), {
      refusalState: this.refusal,
      ignoreLocalErrors: true,
      isCurrent: () => this.baseline() === baseline,
    });
    if (saved) this.onClose();
  }

  protected onClose(): void {
    this.baseline.set(null);
    this.editing.set(null);
  }
}
