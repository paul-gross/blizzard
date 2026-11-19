import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { form } from '@angular/forms/signals';
import { ActivatedRoute, Router } from '@angular/router';
import { asyncState, errorMessage, type KitAsyncStateValue, restingAsyncState, submitForm, ViewportService } from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { type ConfigFailure, configActions, failureFor } from '../config-actions.model';
import { detailCliCommand } from '../config-cli.model';
import type { ConfigFieldView } from '../config-field-tree.model';
import { CredentialForm } from './credential-form';
import { EMPTY_CREDENTIAL_DRAFT, type CredentialDraft } from './credential.domain';
import { credentialRules } from './credential.form';
import { injectCredentialService } from './credential.service';
import { type CredentialEditBaseline, toEditBaseline } from './credential.translate';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { CredentialCheck } from './credential-check';
import { credentialCheckTarget, credentialRecordVm, credentialEditChanges, credentialSettingsUnavailable } from './credentials.model';
import { injectCredentialLifecycleMutation } from './credentials.mutations';
import { injectCredentialQuery, injectCredentialsQuery } from './credentials.query';

/**
 * The selected credential's detail — the right-hand child of `/admin/credentials`, mounted by both
 * its bare child (nothing selected) and `:key`. A container: it reads the record, the records naming
 * it, and its change history, and forwards plain view models to the presentational
 * {@link ConfigRecordPanel}.
 */
@Component({
  selector: 'app-credential-detail', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CredentialForm, ConfigRecordPanel, CredentialCheck], templateUrl: './credential-detail.html',
})
export class CredentialDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly lens = inject(ProjectLens);
  private readonly viewport = inject(ViewportService);
  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), { initialValue: null });
  private readonly credentialQuery = injectCredentialQuery(() => this.key());
  private readonly credentialsQuery = injectCredentialsQuery(() => false);
  private readonly historyQuery = injectConfigHistoryQuery('credential', () => this.credentialQuery.data()?.id ?? null);
  private readonly meQuery = injectMeQuery();
  private readonly service = injectCredentialService();
  private readonly lifecycleMutation = injectCredentialLifecycleMutation();
  private readonly bindings = computed(() => this.credentialsQuery.data()?.bindings ?? []);
  protected readonly fields: readonly ConfigFieldView<CredentialDraft>[] = [
    { key: 'slug', label: 'Slug', kind: 'text' }, { key: 'name', label: 'Name', kind: 'text' },
    { key: 'forge', label: 'Forge', kind: 'select', createOnly: true },
    { key: 'binding', label: 'Binding', kind: 'select', createOnly: true },
    { key: 'secret', label: 'Secret', kind: 'text' },
  ];
  protected readonly vm = computed(() => credentialRecordVm(this.credentialQuery.data()));
  protected readonly state = computed<KitAsyncStateValue>(() => restingAsyncState(this.key() === null, this.credentialQuery, this.vm() === null));
  protected readonly lastChange = computed(() => lastChange(this.historyQuery.data()));
  protected readonly revisions = computed(() => revisionRows(this.historyQuery.data()));
  protected readonly revisionsState = computed<KitAsyncStateValue>(() => asyncState(this.historyQuery, this.revisions().length === 0));
  protected readonly cliCommand = computed(() => detailCliCommand(this.viewport.mode(), 'credential', this.credentialQuery.data()?.slug));
  protected readonly actions = computed(() => configActions(this.viewport.mode(), hasPermission(this.meQuery.data(), 'config:edit'), this.credentialQuery.data()));
  /** The Check action's target — the credential against every record acting with it, shown to an editor. */
  protected readonly check = computed(() =>
    credentialCheckTarget(hasPermission(this.meQuery.data(), 'config:edit'), this.credentialQuery.data()?.id),
  );
  protected readonly baseline = signal<CredentialEditBaseline | null>(null);
  private readonly draft = signal<CredentialDraft>({ ...EMPTY_CREDENTIAL_DRAFT });
  protected readonly editForm = form(this.draft, credentialRules);
  protected readonly unavailable = computed(() => credentialSettingsUnavailable(this.baseline()));
  protected readonly changes = computed(() => credentialEditChanges(this.baseline(), this.draft()));
  private readonly pendingKey = signal<string | null>(null);
  private readonly failure = signal<ConfigFailure | null>(null);
  protected readonly busy = computed(() => this.pendingKey() !== null && this.pendingKey() === this.key());
  protected readonly actionError = computed(() => failureFor(this.failure(), this.key()));

  constructor() {
    // A slug or former slug in the URL opens the credential; the URL then names it by id.
    effect(() => {
      const credential = this.credentialQuery.data();
      if (credential && this.key() !== null && this.key() !== credential.id) {
        void this.router.navigate(this.lens.link(['/admin', 'credentials', credential.id]), { replaceUrl: true, queryParamsHandling: 'preserve' });
      }
    });
  }

  protected onEdit(): void {
    const record = this.credentialQuery.data();
    if (!record) return;
    const baseline = toEditBaseline(record, this.bindings());
    this.editForm().reset(structuredClone(baseline.draft));
    this.baseline.set(baseline);
  }
  protected async onSave(): Promise<void> {
    const baseline = this.baseline();
    if (!baseline) return;
    const saved = await submitForm(this.editForm, (draft) => this.service.edit(baseline, draft));
    if (saved && this.baseline() === baseline) this.baseline.set(null);
  }
  protected onLifecycle(verb: 'retire' | 'enable'): void {
    const record = this.credentialQuery.data();
    if (!record) return;
    this.pendingKey.set(record.id);
    this.failure.set(null);
    this.lifecycleMutation.mutate({ credentialId: record.id, revision: record.revision, retired: verb === 'retire' }, {
      onError: (error) => this.failure.set({ key: record.id, message: errorMessage(error, `${verb} failed.`) }),
      onSettled: () => this.pendingKey.set(null),
    });
  }
}
