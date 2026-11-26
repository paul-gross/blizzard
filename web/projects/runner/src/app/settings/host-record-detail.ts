import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';
import { createRefusalState, type EditBaseline, type SaveOutcome, submitForm } from 'fleet/forms';
import { KitConfirmDialog } from 'fleet/kit/dialog';

import { blankRecordDraft, type HostRecord, type RecordDraft, recordFields } from './host-records.domain';
import { recordFormRules } from './host-records.form';
import { hostRecordVm, retirePrompt } from './host-records.model';
import { injectHostRecordsService } from './host-records.service';
import { toRecordBaseline } from './host-records.translate';
import { whileEditing } from './settings.model';
import { HostRecordDetailView } from './host-record-detail-view';
import { HostRecordFormDialog } from './host-record-form-dialog';
import { type HistoryRef, SettingsHistory } from './settings-history';

/**
 * One workspace's or subscription's detail: its stated keys, its verbs for the superuser, and its history — the
 * host's change log filtered to this record. Edit captures the baseline from the record's read and saves against
 * its revision as `If-Match`; Retire asks first; each verb holds only its own control off while it is in flight.
 */
@Component({
  selector: 'app-host-record-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HostRecordDetailView, HostRecordFormDialog, KitConfirmDialog, SettingsHistory],
  templateUrl: './host-record-detail.html',
})
export class HostRecordDetail {
  readonly record = input.required<HostRecord>();
  /** Whether the session is the host's superuser, who alone writes. */
  readonly superuser = input.required<boolean>();

  private readonly service = injectHostRecordsService(() => this.record().kind);

  protected readonly prefix = computed(() => `records-${this.record().kind}-${this.record().key}`);
  protected readonly vm = computed(() => hostRecordVm(this.record(), this.superuser()));
  protected readonly history = computed(
    (): HistoryRef => ({
      id: `${this.record().kind}-${this.record().key}`,
      title: 'History',
      target: { kind: 'record', recordKind: this.record().kind, key: this.record().key },
    }),
  );
  protected readonly editPending = computed(() => this.service.pending('edit'));
  protected readonly retirePending = computed(() => this.service.pending('retire'));
  protected readonly enablePending = computed(() => this.service.pending('enable'));
  protected readonly verbError = signal<string | null>(null);
  protected readonly confirming = signal(false);
  protected readonly prompt = computed(() => retirePrompt(this.record().kind, this.record().key));

  protected readonly fields = computed(() => recordFields(this.record().kind, 'edit'));
  private readonly baseline = signal<EditBaseline<RecordDraft> | null>(null);
  private readonly draft = signal<RecordDraft>(blankRecordDraft());
  private readonly refusal = createRefusalState<RecordDraft>();
  private readonly tree = form(this.draft, (p) => {
    apply(p, this.refusal.schema);
    apply(p, recordFormRules(() => this.fields(), () => 'edit'));
  });
  protected readonly editForm = computed(() => whileEditing(this.baseline(), this.tree));

  protected onEdit(): void {
    const baseline = toRecordBaseline(this.record());
    if (baseline === null) return;
    const draft = { ...blankRecordDraft(), ...baseline.draft };
    this.refusal.clear();
    this.tree().reset(draft);
    this.draft.set(draft);
    this.baseline.set(baseline);
  }

  protected async onSave(): Promise<void> {
    const baseline = this.baseline();
    if (baseline === null) return;
    const saved = await submitForm(this.tree, (draft) => this.service.edit(baseline, draft), {
      refusalState: this.refusal,
      isCurrent: () => this.baseline() === baseline,
    });
    if (saved) this.onClose();
  }

  protected onClose(): void {
    this.baseline.set(null);
  }

  protected onRetire(): void {
    this.confirming.set(true);
  }

  protected async onRetireConfirmed(): Promise<void> {
    this.confirming.set(false);
    this.verbError.set(null);
    this.settle(await this.service.retire(this.record().key), 'Retire failed.');
  }

  protected async onEnable(): Promise<void> {
    this.verbError.set(null);
    this.settle(await this.service.enable(this.record().key), 'Enable failed.');
  }

  private settle(outcome: SaveOutcome<RecordDraft>, fallback: string): void {
    if (!outcome.ok) this.verbError.set(outcome.form ?? (Object.values(outcome.fields)[0] as string | undefined) ?? fallback);
  }
}
