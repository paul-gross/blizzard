import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { asyncState, errorMessage, KitButton, KitSelect, RecordKind, restingAsyncState, ViewportService } from 'fleet';
import { map } from 'rxjs';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectUrgenciesQuery } from '../../core/urgencies.query';
import { type ConfigFormValues } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { urgencyEdit, urgencyOptions, URGENCY_FIELDS } from './urgencies.model';
import { injectEditUrgencyMutation, injectUrgencyLifecycleMutation } from './urgencies.mutations';
import { urgencyActions, urgencyCliCommand, urgencyDestinations, urgencyFormValues, urgencyRecordVm } from './urgency-detail.model';
import { injectUrgencyIconData, injectUrgencyQuery } from './urgencies.query';

@Component({ selector: 'app-urgency-detail', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, ConfigRecordPanel, KitButton, KitSelect], templateUrl: './urgency-detail.html', styleUrl: './urgency-detail.css' })
export class UrgencyDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly viewport = inject(ViewportService);
  private readonly me = injectMeQuery();
  private readonly all = injectUrgenciesQuery();
  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), { initialValue: null });
  private readonly query = injectUrgencyQuery(() => this.key());
  private readonly image = injectUrgencyIconData(() => this.query.data()?.urgency_id ?? null, () => this.query.data()?.icon?.digest ?? null);
  private readonly history = injectConfigHistoryQuery(RecordKind.URGENCY, () => this.query.data()?.urgency_id ?? null);
  protected readonly editMutation = injectEditUrgencyMutation();
  private readonly lifecycleMutation = injectUrgencyLifecycleMutation();
  protected readonly fields = URGENCY_FIELDS;
  protected readonly editOpen = signal(false);
  protected readonly editRevision = signal<{ key: string; revision: number; shown: ConfigFormValues } | null>(null);
  protected readonly retireOpen = signal(false);
  private readonly retireRevision = signal<{ key: string; revision: number } | null>(null);
  protected readonly editError = signal<string | null>(null);
  protected readonly actionError = signal<string | null>(null);
  protected readonly destination = signal('');
  protected readonly vm = computed(() => urgencyRecordVm(this.query.data()));
  protected readonly shown = computed(() => urgencyFormValues(this.query.data(), this.image.data()));
  protected readonly actions = computed(() => urgencyActions(
    this.viewport.mode(), hasPermission(this.me.data(), 'config:edit'), hasPermission(this.me.data(), 'chunk:control'),
    this.query.data(), this.shown(),
  ));
  protected readonly destinations = computed(() => urgencyDestinations(this.all.data() ?? [], this.query.data()?.urgency_id));
  protected readonly destinationOptions = computed(() => urgencyOptions(this.destinations()));
  protected readonly busy = computed(() => this.lifecycleMutation.isPending());
  protected readonly state = computed(() => restingAsyncState(this.key() === null, this.query, this.vm() === null));
  protected readonly changes = computed(() => revisionRows(this.history.data()));
  protected readonly changesState = computed(() => asyncState(this.history, this.changes().length === 0));
  protected readonly lastChange = computed(() => lastChange(this.history.data()));
  protected readonly record = computed(() => this.query.data());
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly cli = computed(() => urgencyCliCommand(this.viewport.mode(), this.query.data()));
  protected onEdit(): void {
    const record = this.query.data(); const shown = this.shown();
    if (!record || !shown || !this.actions()?.edit) return;
    this.editRevision.set({ key: record.urgency_id, revision: record.revision, shown });
    this.editError.set(null); this.editOpen.set(true);
  }
  protected onSave(values: ConfigFormValues): void {
    const record = this.query.data(); const snapshot = this.editRevision();
    if (!record || !snapshot || snapshot.key !== record.urgency_id || !this.actions()?.edit) return;
    this.editError.set(null);
    this.editMutation.mutate({ key: record.urgency_id, revision: snapshot.revision, body: urgencyEdit(snapshot.shown, values) }, {
      onSuccess: () => this.editOpen.set(false), onError: (e) => this.editError.set(errorMessage(e, 'Save failed.')),
    });
  }
  protected onLifecycle(verb: 'retire' | 'enable'): void {
    const record = this.query.data(); const actions = this.actions();
    if (!record || !actions || (verb === 'retire' && actions.retireBlocked)) return;
    if (verb === 'retire' && record.open_chunks > 0) {
      this.retireRevision.set({ key: record.urgency_id, revision: record.revision });
      this.destination.set(this.destinations().find((u) => u.default)?.urgency_id ?? '');
      this.retireOpen.set(true);
    } else this.submitLifecycle(verb === 'retire');
  }
  protected submitLifecycle(retired: boolean): void {
    const record = this.query.data(); const actions = this.actions();
    if (!record || !actions || (retired && actions.retireBlocked)) return;
    const snapshot = this.retireOpen() ? this.retireRevision() : null;
    if (snapshot && snapshot.key !== record.urgency_id) return;
    if (retired && record.open_chunks > 0 && !this.destinations().some((u) => u.urgency_id === this.destination())) return;
    this.actionError.set(null);
    this.lifecycleMutation.mutate({ key: record.urgency_id, revision: snapshot?.revision ?? record.revision, retired, ...(retired && record.open_chunks > 0 ? { moveTo: this.destination() } : {}) }, {
      onSuccess: () => this.retireOpen.set(false), onError: (e) => this.actionError.set(errorMessage(e, 'Lifecycle change failed.')),
    });
  }
  protected setDestination(value: string): void { this.destination.set(value); }
}
