import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';
import { asyncState } from 'fleet/core';
import { createRefusalState, submitForm } from 'fleet/forms';

import { blankRecordDraft, type HostRecordKind, type RecordDraft, recordFields } from './host-records.domain';
import { recordFormRules } from './host-records.form';
import { hostRecordOf, hostRecordVms, recordsTitle, whileCreating } from './host-records.model';
import { injectHostRecordsQuery } from './host-records.query';
import { injectHostRecordsService } from './host-records.service';
import { HostRecordDetail } from './host-record-detail';
import { HostRecordFormDialog } from './host-record-form-dialog';
import { HostRecordsView } from './host-records-view';

/**
 * One kind's records — Workspaces or Subscriptions — read with the retired beside the active, one record's detail
 * open at a time, and Create for the superuser. A create's dialog stays open on a refusal, keeping the draft.
 */
@Component({
  selector: 'app-host-records',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HostRecordDetail, HostRecordFormDialog, HostRecordsView],
  templateUrl: './host-records.html',
})
export class HostRecordsPanel {
  readonly kind = input.required<HostRecordKind>();
  /** Whether the session is the host's superuser, who alone writes. */
  readonly superuser = input.required<boolean>();

  private readonly query = injectHostRecordsQuery(() => this.kind());
  private readonly service = injectHostRecordsService(() => this.kind());

  protected readonly title = computed(() => recordsTitle(this.kind()));
  private readonly records = computed(() => this.query.data());
  protected readonly rows = computed(() => hostRecordVms(this.records(), this.superuser()));
  protected readonly state = computed(() => asyncState(this.query, this.rows().length === 0));
  protected readonly openKey = signal<string | null>(null);
  protected readonly openRecord = computed(() => hostRecordOf(this.records(), this.openKey()));
  protected readonly createPending = computed(() => this.service.pending('create'));

  protected readonly creating = signal(false);
  protected readonly fields = computed(() => recordFields(this.kind(), 'create'));
  private readonly draft = signal<RecordDraft>(blankRecordDraft());
  private readonly refusal = createRefusalState<RecordDraft>();
  private readonly tree = form(this.draft, (p) => {
    apply(p, this.refusal.schema);
    apply(p, recordFormRules(() => this.fields(), () => 'create'));
  });
  protected readonly createForm = computed(() => whileCreating(this.creating(), this.tree));

  protected onToggle(key: string): void {
    this.openKey.update((open) => (open === key ? null : key));
  }

  protected onCreate(): void {
    const blank = blankRecordDraft();
    this.refusal.clear();
    this.tree().reset({ ...blank });
    this.draft.set(blank);
    this.creating.set(true);
  }

  protected async onSave(): Promise<void> {
    const saved = await submitForm(this.tree, (draft) => this.service.create(draft), {
      refusalState: this.refusal,
      isCurrent: () => this.creating(),
    });
    if (saved) this.creating.set(false);
  }

  protected onClose(): void {
    this.creating.set(false);
  }
}
