import type { HubSettingRow } from './hub-settings.domain';
import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';
import { asyncState, createRefusalState, submitForm } from 'fleet';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import type { HubSettingBaseline, HubSettingDraft } from './hub-settings.domain';
import { hubSettingRules } from './hub-settings.form';
import { changeEntriesOf, changesOf, HUB_SETTING_FIELD_VIEWS, revisionsOf } from './hub-settings.model';
import { injectHubSettingsHistoryQuery, injectHubSettingsQuery } from './hub-settings.query';
import { injectHubSettingsService } from './hub-settings.service';
import { toHubSettingBaseline } from './hub-settings.translate';
import { HubSettingsView } from './hub-settings-view';

@Component({ selector: 'app-hub-settings-page', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, HubSettingsView], templateUrl: './hub-settings-page.html' })
export class HubSettingsPage {
  protected readonly me = injectMeQuery();
  protected readonly allowed = computed(() => hasPermission(this.me.data(), 'settings:admin'));
  protected readonly settings = injectHubSettingsQuery(() => this.allowed());
  protected readonly history = injectHubSettingsHistoryQuery(() => this.allowed());
  private readonly service = injectHubSettingsService();
  protected readonly changes = computed(() => changesOf(this.history.data()?.pages));
  protected readonly revisions = computed(() => revisionsOf(this.changes()));
  protected readonly entries = computed(() => changeEntriesOf(this.changes()));
  protected readonly historyState = computed(() => asyncState(this.history, this.revisions().length === 0));
  protected readonly state = computed(() => asyncState(this.settings, (this.settings.data()?.settings.length ?? 0) === 0));
  protected readonly busy = computed(() => this.service.pending() || this.settings.isFetching());

  protected readonly fields = HUB_SETTING_FIELD_VIEWS;
  /** What the edit started from — `null` while no edit is under way. */
  protected readonly baseline = signal<HubSettingBaseline | null>(null);
  private readonly draft = signal<HubSettingDraft>({ value: '', useDefault: false });
  private readonly refusal = createRefusalState<HubSettingDraft>();
  protected readonly editForm = form(this.draft, (path) => {
    apply(path, this.refusal.schema);
    apply(path, hubSettingRules(() => this.baseline()?.current));
  });

  /** Captures the baseline — key, revision and value from one read — and a working draft apart from it. */
  protected onEdit(row: HubSettingRow): void {
    const revision = this.settings.data()?.revision;
    if (this.busy() || revision === undefined) return;
    const baseline = toHubSettingBaseline(revision, row);
    this.refusal.clear();
    this.baseline.set(baseline);
    this.editForm().reset({ ...baseline.draft });
  }

  /** Sends the draft against the captured baseline; the dialog closes only once the save has settled. */
  protected async onSave(): Promise<void> {
    const baseline = this.baseline();
    if (baseline === null) return;
    const isCurrent = () => this.baseline() === baseline;
    const saved = await submitForm(this.editForm, (draft) => this.service.edit(baseline, draft), {
      refusalState: this.refusal,
      isCurrent,
    });
    if (saved && isCurrent()) this.baseline.set(null);
  }

  protected onCancel(): void {
    this.baseline.set(null);
  }
}
