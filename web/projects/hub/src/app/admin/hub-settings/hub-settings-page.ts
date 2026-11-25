import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { errorMessage, asyncState, type HubSettingRow, parseSettingText, settingValueText } from 'fleet';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { changesOf, revisionsOf } from './hub-settings.model';
import { injectHubSettingsMutation } from './hub-settings.mutations';
import { injectHubSettingsHistoryQuery, injectHubSettingsQuery } from './hub-settings.query';
import { HubSettingsView } from './hub-settings-view';

@Component({ selector: 'app-hub-settings-page', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HubSettingsView], templateUrl: './hub-settings-page.html' })
export class HubSettingsPage {
  protected readonly me = injectMeQuery();
  protected readonly allowed = computed(() => hasPermission(this.me.data(), 'settings:admin'));
  protected readonly settings = injectHubSettingsQuery(() => this.allowed());
  protected readonly history = injectHubSettingsHistoryQuery(() => this.allowed());
  protected readonly mutation = injectHubSettingsMutation();
  protected readonly editing = signal<{ row: HubSettingRow; revision: number } | null>(null);
  protected readonly value = signal('');
  protected readonly failure = signal<string | null>(null);
  protected readonly changes = computed(() => changesOf(this.history.data()?.pages));
  protected readonly revisions = computed(() => revisionsOf(this.changes()));
  protected readonly historyState = computed(() => asyncState(this.history, this.revisions().length === 0));
  protected readonly state = computed(() => asyncState(this.settings, (this.settings.data()?.settings.length ?? 0) === 0));
  protected readonly busy = computed(() => this.mutation.isPending() || this.settings.isFetching());
  protected edit(row: HubSettingRow): void {
    const revision = this.settings.data()?.revision;
    if (this.busy() || revision === undefined) return;
    this.editing.set({ row, revision }); this.value.set(settingValueText(row.value)); this.failure.set(null);
  }
  protected cancel(): void { if (!this.busy()) this.editing.set(null); }
  protected save(unset = false): void {
    const edit = this.editing(); if (!edit || !this.allowed() || this.busy()) return;
    let value: unknown = this.value();
    if (!unset) {
      const parsed = parseSettingText(edit.row.value, this.value());
      if ('error' in parsed) { this.failure.set(parsed.error); return; }
      value = parsed.value;
    }
    this.failure.set(null);
    this.mutation.mutate({ key: edit.row.key, revision: edit.revision, value, unset }, {
      onSuccess: () => this.editing.set(null), onError: (error) => this.failure.set(errorMessage(error, 'Save failed.')),
    });
  }
}
