import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { form } from '@angular/forms/signals';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import {
  type EditBaseline,
  errorMessage,
  injectPendingMutationVariables,
  isPendingFor,
  type KitAsyncStateValue,
  restingAsyncState,
  submitForm,
} from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { cadenceLifecycleMutationKey, editCadenceMutationKey } from '../../core/mutation-keys';
import { injectUrgenciesQuery } from '../../core/urgencies.query';
import { injectHubCadenceErrandsQuery, injectHubCadencesQuery } from '../core/cadences.query';
import { type Cadence, type CadenceDraft, type ChoreDraft, EMPTY_CADENCE_DRAFT } from '../core/cadence.domain';
import { choresAdding, choresRemoving, choresReplacing, toEditBaseline } from '../core/cadence.translate';
import { cadenceEditRules } from './cadence.form';
import { injectCadenceService } from './cadence.service';
import { type CadenceEditVars, injectEditCadenceMutation } from './cadence-edit.mutations';
import { type CadenceLifecycleVars, injectCadenceLifecycleMutation } from './cadence-lifecycle.mutations';
import { type CadencePanelVm, type ChoreEditEvent, FleetCadencePanel } from './cadence-panel';
import { cadenceBySlug, cadenceOverrideRetired, cadencePanelVm, whileEditing } from './gardening-cadence-detail.model';

/**
 * The selected cadence's detail — the right-hand child of `/gardening/cadences`. Mounted by both of that route's
 * children: the bare one, where it renders its own "nothing selected" state, and `:cadenceSlug`.
 *
 * It resolves the slug against the project's cadence list — the same cache-keyed read the list beside it holds — and
 * reads that cadence's errands by id. A container: it injects the reads and the edit and lifecycle mutations, and
 * forwards a plain view model to the presentational {@link FleetCadencePanel}.
 *
 * It owns the cadence edit: Edit captures the baseline — id, revision, and draft from one read — and a working copy of
 * its draft, and Save sends the working draft against that baseline, so a refresh of the read changes neither. A
 * refused save keeps both; Cancel, or selecting another cadence, ends the edit, and the next starts from a fresh read.
 *
 * A chore add, edit, or remove, and a retire or enable, is sent against the revision the cadence now reads at. A chore
 * change sends the whole chore list, rebuilt from the served chores with the one change.
 */
@Component({
  selector: 'app-gardening-cadence-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetCadencePanel],
  templateUrl: './gardening-cadence-detail.html',
  styleUrl: '../core/gardening-detail-host.css',
})
export class GardeningCadenceDetail {
  protected readonly urgenciesQuery = injectUrgenciesQuery();
  private readonly route = inject(ActivatedRoute);

  private readonly cadencesQuery = injectHubCadencesQuery();
  private readonly meQuery = injectMeQuery();
  private readonly editMutation = injectEditCadenceMutation();
  private readonly cadenceService = injectCadenceService();
  private readonly lifecycleMutation = injectCadenceLifecycleMutation();

  private readonly pendingEdits = injectPendingMutationVariables<CadenceEditVars>(editCadenceMutationKey);
  private readonly pendingLifecycle = injectPendingMutationVariables<CadenceLifecycleVars>(cadenceLifecycleMutationKey);

  private readonly cadenceSlug = toSignal(this.route.paramMap.pipe(map((params) => params.get('cadenceSlug'))), {
    initialValue: null,
  });

  private readonly selected = computed<Cadence | null>(() =>
    cadenceBySlug(this.cadenceSlug(), this.cadencesQuery.data() ?? []),
  );

  private readonly errandsQuery = injectHubCadenceErrandsQuery(() => this.selected()?.id ?? null);

  protected readonly canEdit = computed(() => hasPermission(this.meQuery.data(), 'graph:edit'));

  protected readonly panelVm = computed<CadencePanelVm | null>(() => {
    const cadence = this.selected();
    return cadencePanelVm(cadence, cadenceOverrideRetired(cadence, this.pendingLifecycle()), this.errandsQuery.data(), this.errandsQuery);
  });

  protected readonly panelState = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.cadenceSlug() === null, this.cadencesQuery, this.selected() === null),
  );

  protected readonly actionError = signal<string | null>(null);

  protected readonly editPending = computed(() =>
    isPendingFor(this.pendingEdits(), (v) => v.cadenceId === this.selected()?.id),
  );

  protected readonly lifecyclePending = computed(() =>
    isPendingFor(this.pendingLifecycle(), (v) => v.cadenceId === this.selected()?.id),
  );

  /** What the cadence edit started from — `null` while no edit is under way. */
  private readonly editBaseline = signal<EditBaseline<CadenceDraft> | null>(null);
  private readonly editDraft = signal<CadenceDraft>(EMPTY_CADENCE_DRAFT);
  private readonly editTree = form(this.editDraft, cadenceEditRules);

  /** The edit's field tree while an edit is under way. */
  protected readonly editForm = computed(() => whileEditing(this.editBaseline(), this.editTree));

  protected onStartEdit(): void {
    const cadence = this.selected();
    if (cadence === null) return;
    const baseline = toEditBaseline(cadence);
    this.editTree().reset({ ...baseline.draft });
    this.editBaseline.set(baseline);
  }

  protected async onSaveEdit(): Promise<void> {
    const baseline = this.editBaseline();
    if (baseline === null) return;
    const saved = await submitForm(this.editTree, (draft) => this.cadenceService.edit(baseline, draft));
    if (saved && this.editBaseline() === baseline) this.editBaseline.set(null);
  }

  protected onCancelEdit(): void {
    this.editBaseline.set(null);
  }

  protected onAddChore(draft: ChoreDraft): void {
    const cadence = this.selected();
    if (cadence !== null) this.send({ chores: choresAdding(cadence.chores, draft) }, 'Add chore failed.');
  }

  protected onEditChore(event: ChoreEditEvent): void {
    const cadence = this.selected();
    if (cadence !== null) this.send({ chores: choresReplacing(cadence.chores, event.key, event.draft) }, 'Save chore failed.');
  }

  protected onRemoveChore(key: string): void {
    const cadence = this.selected();
    if (cadence !== null) this.send({ chores: choresRemoving(cadence.chores, key) }, 'Remove chore failed.');
  }

  protected onRetire(): void {
    this.lifecycle(true, 'Retire failed.');
  }

  protected onEnable(): void {
    this.lifecycle(false, 'Enable failed.');
  }

  constructor() {
    // Selecting another cadence ends the edit.
    effect(() => {
      this.cadenceSlug();
      untracked(() => this.editBaseline.set(null));
    });
  }

  private send(body: CadenceEditVars['body'], fallback: string): void {
    const cadence = this.selected();
    if (cadence === null) return;
    this.actionError.set(null);
    this.editMutation.mutate(
      { cadenceId: cadence.id, revision: cadence.revision, body },
      { onError: (error: unknown) => this.actionError.set(errorMessage(error, fallback)) },
    );
  }

  private lifecycle(retired: boolean, fallback: string): void {
    const cadence = this.selected();
    if (cadence === null) return;
    this.actionError.set(null);
    this.lifecycleMutation.mutate(
      { cadenceId: cadence.id, revision: cadence.revision, retired },
      { onError: (error: unknown) => this.actionError.set(errorMessage(error, fallback)) },
    );
  }
}
