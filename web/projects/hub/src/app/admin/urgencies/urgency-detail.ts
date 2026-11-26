import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { asyncState, errorMessage, restingAsyncState } from 'fleet/core';
import { submitForm, type EditBaseline } from 'fleet/forms';
import { ViewportService } from 'fleet/viewport';
import { map } from 'rxjs';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectDomainUrgenciesQuery } from '../../core/urgencies.query';
import { EMPTY_URGENCY_DRAFT, type UrgencyDraft } from './urgency.domain';
import { urgencyEditRules } from './urgency.form';
import { toDraft, toEditBaseline, URGENCY_RECORD_KIND } from './urgency.translate';
import { injectUrgencyService } from './urgency.service';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { urgencyOptions } from './urgencies.model';
import { injectUrgencyLifecycleMutation } from './urgencies.mutations';
import { urgencyActions, urgencyCliCommand, urgencyDestinations, urgencyImageError, urgencyImageUrl, urgencyRecordVm } from './urgency-detail.model';
import { injectDomainUrgencyQuery } from './urgencies.query';
import { injectUrgencyIconDataUrlState } from '../../core/urgency-icon.query';
import { UrgencyDetailView } from './urgency-detail-view';

@Component({ selector: 'app-urgency-detail', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [UrgencyDetailView], templateUrl: './urgency-detail.html' })
export class UrgencyDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly viewport = inject(ViewportService);
  private readonly me = injectMeQuery();
  private readonly all = injectDomainUrgenciesQuery();
  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), { initialValue: null });
  private readonly query = injectDomainUrgencyQuery(() => this.key());
  private readonly image = injectUrgencyIconDataUrlState(() => {
    const u = this.query.data();
    return u?.icon ? { key: u.id, digest: u.icon.digest } : null;
  });
  private readonly history = injectConfigHistoryQuery(URGENCY_RECORD_KIND, () => this.query.data()?.id ?? null);
  private readonly urgencyService = injectUrgencyService();
  private readonly editDraft = signal<UrgencyDraft>({ ...EMPTY_URGENCY_DRAFT });
  protected readonly editForm = form(this.editDraft, urgencyEditRules);
  private readonly lifecycleMutation = injectUrgencyLifecycleMutation();
  protected readonly editOpen = signal(false);
  protected readonly editRevision = signal<EditBaseline<UrgencyDraft> | null>(null);
  protected readonly retireOpen = signal(false);
  protected readonly retireRevision = signal<{ key: string; revision: number; openChunks: number } | null>(null);
  protected readonly actionError = signal<string | null>(null);
  protected readonly destination = signal('');
  protected readonly vm = computed(() => urgencyRecordVm(this.query.data()));
  protected readonly shown = computed(() => toDraft(this.query.data(), urgencyImageUrl(this.image())));
  protected readonly actions = computed(() => urgencyActions(
    this.viewport.mode(), hasPermission(this.me.data(), 'config:edit'), hasPermission(this.me.data(), 'chunk:control'),
    this.query.data(), this.shown(),
  ));
  protected readonly imageError = computed(() => urgencyImageError(this.query.data(), this.actions(), this.image()));
  protected readonly destinations = computed(() => urgencyDestinations(this.all.data() ?? [], this.query.data()?.id));
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
    this.editRevision.set(toEditBaseline(record, shown));
    this.editForm().reset({ ...shown }); this.editOpen.set(true);
  }
  protected async onSave(): Promise<void> {
    const record = this.query.data(); const baseline = this.editRevision();
    if (!record || !baseline || baseline.id !== record.id || !this.actions()) return;
    if (await submitForm(this.editForm, (draft) => this.urgencyService.edit(baseline, draft))) this.editOpen.set(false);
  }
  protected onLifecycle(verb: 'retire' | 'enable'): void {
    const record = this.query.data(); const actions = this.actions();
    if (!record || !actions || this.busy() || (verb === 'retire' && actions.retireBlocked)) return;
    if (verb === 'retire') {
      this.retireRevision.set({ key: record.id, revision: record.revision, openChunks: record.openChunks });
      this.destination.set(this.destinations().find((u) => u.default)?.id ?? '');
      this.actionError.set(null);
      this.retireOpen.set(true);
    } else this.submitLifecycle(false);
  }
  protected submitLifecycle(retired: boolean): void {
    const record = this.query.data(); const actions = this.actions();
    if (!record || !actions || this.busy() || (retired && actions.retireBlocked)) return;
    const snapshot = this.retireOpen() ? this.retireRevision() : null;
    if (snapshot && snapshot.key !== record.id) return;
    if (retired && (snapshot?.openChunks ?? record.openChunks) > 0 && !this.destinations().some((u) => u.id === this.destination())) return;
    this.actionError.set(null);
    this.lifecycleMutation.mutate({ key: record.id, revision: snapshot?.revision ?? record.revision, retired, ...(retired && (snapshot?.openChunks ?? record.openChunks) > 0 ? { moveTo: this.destination() } : {}) }, {
      onSuccess: () => this.retireOpen.set(false), onError: (e) => this.actionError.set(errorMessage(e, 'Lifecycle change failed.')),
    });
  }
  protected setDestination(value: string): void { this.destination.set(value); }
}
