import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import {
  type CadenceEditRequest,
  type CadenceView,
  errorMessage,
  injectPendingMutationVariables,
  isPendingFor,
  type KitAsyncStateValue,
  restingAsyncState,
} from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { cadenceLifecycleMutationKey, editCadenceMutationKey } from '../../core/mutation-keys';
import { injectHubCadenceErrandsQuery, injectHubCadencesQuery } from '../core/cadences.query';
import type { CadenceDraft } from './cadence-form';
import { type CadenceEditVars, injectEditCadenceMutation } from './cadence-edit.mutations';
import { type CadenceLifecycleVars, injectCadenceLifecycleMutation } from './cadence-lifecycle.mutations';
import { type CadencePanelVm, type ChoreEditEvent, FleetCadencePanel } from './cadence-panel';
import type { ChoreDraft } from './chore-form';
import {
  cadenceBySlug,
  cadenceOverrideRetired,
  cadencePanelVm,
  choresAdding,
  choresRemoving,
  choresReplacing,
  editCadenceRequest,
} from './gardening-cadence-detail.model';

/**
 * The selected cadence's detail — the right-hand child of `/gardening/cadences`. Mounted by both of that route's
 * children: the bare one, where it renders its own "nothing selected" state, and `:cadenceSlug`.
 *
 * It resolves the slug against the project's cadence list — the same cache-keyed read the list beside it holds — and
 * reads that cadence's errands by id. A container: it injects the reads and the edit and lifecycle mutations, and
 * forwards a plain view model to the presentational {@link FleetCadencePanel}.
 *
 * Every write is sent against the revision the cadence was read at. A chore add, edit, or remove sends the whole chore
 * list, rebuilt from the served chores with the one change.
 */
@Component({
  selector: 'app-gardening-cadence-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetCadencePanel],
  templateUrl: './gardening-cadence-detail.html',
  styleUrl: '../core/gardening-detail-host.css',
})
export class GardeningCadenceDetail {
  private readonly route = inject(ActivatedRoute);

  private readonly cadencesQuery = injectHubCadencesQuery();
  private readonly meQuery = injectMeQuery();
  private readonly editMutation = injectEditCadenceMutation();
  private readonly lifecycleMutation = injectCadenceLifecycleMutation();

  private readonly pendingEdits = injectPendingMutationVariables<CadenceEditVars>(editCadenceMutationKey);
  private readonly pendingLifecycle = injectPendingMutationVariables<CadenceLifecycleVars>(cadenceLifecycleMutationKey);

  private readonly cadenceSlug = toSignal(this.route.paramMap.pipe(map((params) => params.get('cadenceSlug'))), {
    initialValue: null,
  });

  private readonly selected = computed<CadenceView | null>(() =>
    cadenceBySlug(this.cadenceSlug(), this.cadencesQuery.data() ?? []),
  );

  private readonly errandsQuery = injectHubCadenceErrandsQuery(() => this.selected()?.cadence_id ?? null);

  protected readonly canEdit = computed(() => hasPermission(this.meQuery.data(), 'graph:edit'));

  protected readonly panelVm = computed<CadencePanelVm | null>(() => {
    const cadence = this.selected();
    return cadencePanelVm(cadence, cadenceOverrideRetired(cadence, this.pendingLifecycle()), this.errandsQuery.data());
  });

  protected readonly panelState = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.cadenceSlug() === null, this.cadencesQuery, this.selected() === null),
  );

  protected readonly actionError = signal<string | null>(null);

  protected readonly editPending = computed(() =>
    isPendingFor(this.pendingEdits(), (v) => v.cadenceId === this.selected()?.cadence_id),
  );

  protected readonly lifecyclePending = computed(() =>
    isPendingFor(this.pendingLifecycle(), (v) => v.cadenceId === this.selected()?.cadence_id),
  );

  protected onEdit(draft: CadenceDraft): void {
    this.send(editCadenceRequest(draft), 'Save failed.');
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

  private send(body: CadenceEditRequest, fallback: string): void {
    const cadence = this.selected();
    if (cadence === null) return;
    this.actionError.set(null);
    this.editMutation.mutate(
      { cadenceId: cadence.cadence_id, revision: cadence.revision, body },
      { onError: (error: unknown) => this.actionError.set(errorMessage(error, fallback)) },
    );
  }

  private lifecycle(retired: boolean, fallback: string): void {
    const cadence = this.selected();
    if (cadence === null) return;
    this.actionError.set(null);
    this.lifecycleMutation.mutate(
      { cadenceId: cadence.cadence_id, revision: cadence.revision, retired },
      { onError: (error: unknown) => this.actionError.set(errorMessage(error, fallback)) },
    );
  }
}
