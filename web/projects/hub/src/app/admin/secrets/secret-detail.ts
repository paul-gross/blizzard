import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { form } from '@angular/forms/signals';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import {
  asyncState,
  type EditBaseline,
  errorMessage,
  type KitAsyncStateValue,
  restingAsyncState,
  submitForm,
  ViewportService,
} from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { type ConfigFailure, configActions, failureFor } from '../config-actions.model';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import { detailCliCommand } from '../config-cli.model';
import { historyRecordKey, lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { EMPTY_SECRET_REPLACE_DRAFT, type SecretReplaceDraft } from './secret.domain';
import { secretReplaceRules } from './secret.form';
import { injectSecretService } from './secret.service';
import { SECRET_RECORD_KIND, toReplaceBaseline } from './secret.translate';
import { SECRET_REPLACE_FIELD_VIEWS, secretRecordVm } from './secrets.model';
import { injectSecretLifecycleMutation } from './secrets.mutations';
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
  imports: [ConfigFieldTreeDialog, ConfigRecordPanel],
  templateUrl: './secret-detail.html',
})
export class SecretDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly viewport = inject(ViewportService);

  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), {
    initialValue: null,
  });
  private readonly secretQuery = injectSecretQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery(SECRET_RECORD_KIND, () =>
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
    detailCliCommand(this.viewport.mode(), SECRET_RECORD_KIND, this.secretQuery.data()),
  );

  private readonly meQuery = injectMeQuery();
  private readonly secretService = injectSecretService();
  private readonly lifecycleMutation = injectSecretLifecycleMutation();

  protected readonly fields = SECRET_REPLACE_FIELD_VIEWS;
  /** What the replacement started from — `null` while no replacement is under way. */
  protected readonly replaceBaseline = signal<EditBaseline<SecretReplaceDraft> | null>(null);
  private readonly replaceDraft = signal<SecretReplaceDraft>(EMPTY_SECRET_REPLACE_DRAFT);
  protected readonly replaceForm = form(this.replaceDraft, secretReplaceRules);
  private readonly pendingKey = signal<string | null>(null);
  private readonly failure = signal<ConfigFailure | null>(null);

  protected readonly actions = computed(() => {
    const secret = this.secretQuery.data();
    return configActions(this.viewport.mode(), hasPermission(this.meQuery.data(), 'config:edit'), secret, {
      replace: true,
    });
  });
  protected readonly busy = computed(() => this.pendingKey() !== null && this.pendingKey() === this.key());
  protected readonly actionError = computed(() => failureFor(this.failure(), this.key()));

  constructor() {
    // Selecting another secret ends the replacement.
    effect(() => {
      this.key();
      untracked(() => this.replaceBaseline.set(null));
    });
  }

  /** Captures the baseline — name and revision from one read — and a blank working draft: the value is never read. */
  protected onReplace(): void {
    const secret = this.secretQuery.data();
    if (!secret) return;
    this.replaceForm().reset({ ...EMPTY_SECRET_REPLACE_DRAFT });
    this.replaceBaseline.set(toReplaceBaseline(secret));
  }

  /** Sends the typed value against the baseline's revision; the dialog closes only once the save has settled. */
  protected async onSave(): Promise<void> {
    const baseline = this.replaceBaseline();
    if (baseline === null) return;
    const saved = await submitForm(this.replaceForm, (draft) => this.secretService.replace(baseline, draft));
    if (saved && this.replaceBaseline() === baseline) this.replaceBaseline.set(null);
  }

  protected onCancel(): void {
    this.replaceBaseline.set(null);
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
