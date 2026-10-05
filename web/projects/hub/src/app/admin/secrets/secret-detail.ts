import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import {
  asyncState,
  errorMessage,
  type KitAsyncStateValue,
  RecordKind,
  restingAsyncState,
  ViewportService,
} from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { type ConfigFailure, configActions, failureFor } from '../config-actions.model';
import { ConfigFormDialog } from '../config-form-dialog';
import type { ConfigFormValues } from '../config-edit.model';
import { detailCliCommand } from '../config-cli.model';
import { historyRecordKey, lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { SECRET_REPLACE_FIELDS, secretRecordVm } from './secrets.model';
import { injectReplaceSecretMutation, injectSecretLifecycleMutation } from './secrets.mutations';
import { injectSecretQuery } from './secrets.query';

/**
 * The selected secret's detail — the right-hand child of `/admin/secrets`,
 * mounted by both its bare child (nothing selected) and `:key`. A container: it reads
 * the record and its change history and forwards plain view models to the
 * presentational {@link ConfigRecordPanel}.
 */
@Component({
  selector: 'app-secret-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, ConfigRecordPanel],
  templateUrl: './secret-detail.html',
})
export class SecretDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly viewport = inject(ViewportService);

  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), {
    initialValue: null,
  });
  private readonly secretQuery = injectSecretQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery(RecordKind.SECRET, () =>
    historyRecordKey(this.secretQuery.data()),
  );

  protected readonly vm = computed(() => secretRecordVm(this.secretQuery.data()));
  protected readonly state = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.key() === null, this.secretQuery, this.vm() === null),
  );
  protected readonly lastChange = computed(() => lastChange(this.historyQuery.data()));
  protected readonly revisions = computed(() => revisionRows(this.historyQuery.data()));
  protected readonly revisionsState = computed<KitAsyncStateValue>(() =>
    asyncState(this.historyQuery, this.revisions().length === 0),
  );
  protected readonly cliCommand = computed(() =>
    detailCliCommand(this.viewport.mode(), RecordKind.SECRET, this.secretQuery.data()),
  );

  private readonly meQuery = injectMeQuery();
  protected readonly replaceMutation = injectReplaceSecretMutation();
  private readonly lifecycleMutation = injectSecretLifecycleMutation();

  protected readonly fields = SECRET_REPLACE_FIELDS;
  protected readonly replaceOpen = signal(false);
  protected readonly replaceError = signal<string | null>(null);
  private readonly pendingKey = signal<string | null>(null);
  private readonly failure = signal<ConfigFailure | null>(null);

  protected readonly actions = computed(() => {
    const secret = this.secretQuery.data();
    return configActions(this.viewport.mode(), hasPermission(this.meQuery.data(), 'config:edit'), secret, {
      replace: true,
      referenceCount: (secret?.references ?? []).length,
    });
  });
  protected readonly busy = computed(() => this.pendingKey() !== null && this.pendingKey() === this.key());
  protected readonly actionError = computed(() => failureFor(this.failure(), this.key()));

  protected onReplace(): void {
    this.replaceError.set(null);
    this.replaceOpen.set(true);
  }

  protected onSave(values: ConfigFormValues): void {
    const secret = this.secretQuery.data();
    const value = values['value'];
    if (!secret || typeof value !== 'string') return;
    this.replaceError.set(null);
    this.replaceMutation.mutate(
      { name: secret.name, revision: secret.revision, value },
      {
        onSuccess: () => this.replaceOpen.set(false),
        onError: (error) => this.replaceError.set(errorMessage(error, 'Replace failed.')),
      },
    );
  }

  protected onLifecycle(verb: 'retire' | 'enable'): void {
    const secret = this.secretQuery.data();
    if (!secret) return;
    this.pendingKey.set(secret.name);
    this.failure.set(null);
    this.lifecycleMutation.mutate(
      { name: secret.name, retired: verb === 'retire' },
      {
        onError: (error) =>
          this.failure.set({
            key: secret.name,
            message: errorMessage(error, `${verb} failed.`),
          }),
        onSettled: () => this.pendingKey.set(null),
      },
    );
  }
}
