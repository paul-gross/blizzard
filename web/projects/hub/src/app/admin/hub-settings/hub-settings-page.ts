import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { injectInfiniteQuery, injectMutation, injectQuery, QueryClient } from '@tanstack/angular-query-experimental';
import { changesApiAdminSettingsChangesGet, getSettingsApiAdminSettingsGet, patchSettingsApiAdminSettingsPatch, errorMessage, asyncState, KitButton, KitTextInput, type HubSettingRow, type HubSettingsChange, type FieldChangeView } from 'fleet';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ConfigRecordPanel, type ConfigRecordVm } from '../config-record-panel';
import { ConfigRevisions } from '../config-revisions';
import { ConfigFieldDiff } from '../config-field-diff';

@Component({
  selector: 'app-hub-settings-page', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitTextInput, ConfigRecordPanel, ConfigRevisions, ConfigFieldDiff], templateUrl: './hub-settings-page.html',
})
export class HubSettingsPage {
  protected readonly me = injectMeQuery();
  private readonly client = inject(QueryClient);
  protected readonly allowed = computed(() => hasPermission(this.me.data(), 'settings:admin'));
  protected readonly settings = injectQuery(() => ({
    queryKey: ['hub', 'admin-settings'], enabled: this.allowed(),
    queryFn: async () => { const { data, error } = await getSettingsApiAdminSettingsGet(); if (error) throw error; return data!; },
  }));
  protected readonly history = injectInfiniteQuery(() => ({
    queryKey: ['hub', 'admin-settings-history'], enabled: this.allowed(),
    initialPageParam: undefined as number | undefined,
    queryFn: async ({ pageParam }: { pageParam: number | undefined }) => { const { data, error } = await changesApiAdminSettingsChangesGet({ query: { before: pageParam } }); if (error) throw error; return data!; },
    getNextPageParam: (last) => last.next_before ?? undefined,
  }));
  protected readonly mutation = injectMutation(() => ({
    mutationFn: async (v: { key: string; revision: number; value: unknown; unset: boolean }) => {
      const { error } = await patchSettingsApiAdminSettingsPatch({ headers: { 'if-match': v.revision }, body: v.unset ? { unset: [v.key] } : { set: { [v.key]: v.value } } });
      if (error) throw error;
    },
    onSettled: () => { void this.client.invalidateQueries({ queryKey: ['hub', 'admin-settings'] }); void this.client.invalidateQueries({ queryKey: ['hub', 'admin-settings-history'] }); },
  }));
  protected readonly editing = signal<{ row: HubSettingRow; revision: number } | null>(null);
  protected readonly value = signal('');
  protected readonly failure = signal<string | null>(null);
  protected readonly changes = computed(() => this.history.data()?.pages.flatMap((page) => page.changes) ?? []);
  protected readonly revisions = computed(() => this.changes().map((c) => ({ id: c.id, revision: c.revision, op: c.op, fields: '', at: c.recorded_at, actor: c.actor, door: c.door })));
  protected readonly historyState = computed(() => asyncState(this.history, this.revisions().length === 0));
  protected record(row: HubSettingRow): ConfigRecordVm {
    return { name: row.key, badges: [], facts: [{ label: 'Value', value: this.display(row.value) }, { label: 'Source', value: row.source }, { label: 'Lifetime', value: row.lifetime }], revision: row.revision, note: null, links: null, hasHistory: false };
  }
  protected diff(change: HubSettingsChange): FieldChangeView[] {
    const value = (entry: unknown): unknown => entry && typeof entry === 'object' && 'value' in entry ? entry.value : entry;
    return change.diff.map((d) => ({ field: String(d['key']), old: value(d['old']), new: value(d['new']), old_label: null, new_label: null }));
  }
  protected display(value: unknown): string { return typeof value === 'string' ? value : JSON.stringify(value); }
  protected edit(row: HubSettingRow): void {
    const revision = this.settings.data()?.revision;
    if (revision === undefined || row.lifetime !== 'pass') return;
    this.editing.set({ row, revision }); this.value.set(this.display(row.value)); this.failure.set(null);
  }
  protected save(unset = false): void {
    const edit = this.editing(); if (!edit || !this.allowed()) return;
    let value: unknown = this.value();
    if (!unset && typeof edit.row.value !== 'string') {
      try { value = JSON.parse(this.value()); } catch { this.failure.set('Enter a valid JSON value.'); return; }
    }
    this.failure.set(null);
    this.mutation.mutate({ key: edit.row.key, revision: edit.revision, value, unset }, {
      onSuccess: () => this.editing.set(null), onError: (error) => this.failure.set(errorMessage(error, 'Save failed.')),
    });
  }
}
