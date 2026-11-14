import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { asyncState, errorMessage, type KitAsyncStateValue, RecordKind, restingAsyncState, ViewportService } from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { type ConfigFailure, configActions, failureFor } from '../config-actions.model';
import { detailCliCommand } from '../config-cli.model';
import { type ConfigFormValues, configEdit, shownFormValues } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { injectForgesQuery } from '../forges/forges.query';
import { credentialFields, credentialPatch, credentialRecordVm, credentialShown } from './credentials.model';
import { injectCredentialLifecycleMutation, injectEditCredentialMutation } from './credentials.mutations';
import { injectCredentialQuery, injectCredentialsQuery } from './credentials.query';

/**
 * The selected credential's detail — the right-hand child of `/admin/credentials`, mounted by both
 * its bare child (nothing selected) and `:key`. A container: it reads the record, the records naming
 * it, and its change history, and forwards plain view models to the presentational
 * {@link ConfigRecordPanel}.
 */
@Component({
  selector: 'app-credential-detail', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, ConfigRecordPanel], templateUrl: './credential-detail.html',
})
export class CredentialDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly lens = inject(ProjectLens);
  private readonly viewport = inject(ViewportService);
  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), { initialValue: null });
  private readonly credentialQuery = injectCredentialQuery(() => this.key());
  private readonly credentialsQuery = injectCredentialsQuery(() => false);
  private readonly forgesQuery = injectForgesQuery(() => false);
  private readonly historyQuery = injectConfigHistoryQuery(RecordKind.CREDENTIAL, () => this.credentialQuery.data()?.credential_id ?? null);
  private readonly meQuery = injectMeQuery();
  protected readonly editMutation = injectEditCredentialMutation();
  private readonly lifecycleMutation = injectCredentialLifecycleMutation();
  private readonly bindings = computed(() => this.credentialsQuery.data()?.bindings ?? []);
  private readonly forgeBindings = computed(() => this.credentialsQuery.data()?.forge_bindings ?? []);
  protected readonly fields = computed(() => credentialFields(this.forgesQuery.data() ?? [], this.bindings(), this.forgeBindings()));
  protected readonly vm = computed(() => credentialRecordVm(this.credentialQuery.data()));
  protected readonly state = computed<KitAsyncStateValue>(() => restingAsyncState(this.key() === null, this.credentialQuery, this.vm() === null));
  protected readonly lastChange = computed(() => lastChange(this.historyQuery.data()));
  protected readonly revisions = computed(() => revisionRows(this.historyQuery.data()));
  protected readonly revisionsState = computed<KitAsyncStateValue>(() => asyncState(this.historyQuery, this.revisions().length === 0));
  protected readonly cliCommand = computed(() => detailCliCommand(this.viewport.mode(), RecordKind.CREDENTIAL, this.credentialQuery.data()?.slug));
  protected readonly actions = computed(() => configActions(this.viewport.mode(), hasPermission(this.meQuery.data(), 'config:edit'), this.credentialQuery.data()));
  protected readonly shown = computed(() => {
    const record = this.credentialQuery.data();
    return shownFormValues(this.fields(), record && credentialShown(record));
  });
  protected readonly editOpen = signal(false);
  protected readonly editError = signal<string | null>(null);
  private readonly pendingKey = signal<string | null>(null);
  private readonly failure = signal<ConfigFailure | null>(null);
  protected readonly busy = computed(() => this.pendingKey() !== null && this.pendingKey() === this.key());
  protected readonly actionError = computed(() => failureFor(this.failure(), this.key()));

  constructor() {
    // A slug or former slug in the URL opens the credential; the URL then names it by id.
    effect(() => {
      const credential = this.credentialQuery.data();
      if (credential && this.key() !== null && this.key() !== credential.credential_id) {
        void this.router.navigate(this.lens.link(['/admin', 'credentials', credential.credential_id]), { replaceUrl: true, queryParamsHandling: 'preserve' });
      }
    });
  }

  protected onEdit(): void {
    this.editError.set(null);
    this.editOpen.set(true);
  }
  protected onSave(values: ConfigFormValues): void {
    const record = this.credentialQuery.data();
    const shown = this.shown();
    if (!record || !shown) return;
    this.editError.set(null);
    const body = credentialPatch(configEdit(this.fields(), shown, values).body, values, record.binding, this.bindings());
    this.editMutation.mutate({ credentialId: record.credential_id, revision: record.revision, body }, {
      onSuccess: () => this.editOpen.set(false),
      onError: (error) => this.editError.set(errorMessage(error, 'Save failed.')),
    });
  }
  protected onLifecycle(verb: 'retire' | 'enable'): void {
    const record = this.credentialQuery.data();
    if (!record) return;
    this.pendingKey.set(record.credential_id);
    this.failure.set(null);
    this.lifecycleMutation.mutate({ credentialId: record.credential_id, revision: record.revision, retired: verb === 'retire' }, {
      onError: (error) => this.failure.set({ key: record.credential_id, message: errorMessage(error, `${verb} failed.`) }),
      onSettled: () => this.pendingKey.set(null),
    });
  }
}
