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
import { type Cadence, type CadenceDraft, type ChoreDraft, EMPTY_CADENCE_DRAFT, EMPTY_CHORE_DRAFT } from '../core/cadence.domain';
import { toChoreDraft, toChoresBaseline, toEditBaseline } from '../core/cadence.translate';
import { cadenceEditRules } from './cadence.form';
import { choreAddRules, choreEditRules } from './chore.form';
import { type ChoresBaseline, injectChoreService } from './chore.service';
import { injectCadenceService } from './cadence.service';
import type { CadenceEditVars } from './cadence-edit.mutations';
import { type CadenceLifecycleVars, injectCadenceLifecycleMutation } from './cadence-lifecycle.mutations';
import { type CadencePanelVm, FleetCadencePanel } from './cadence-panel';
import {
  baselineChore,
  cadenceBySlug,
  cadenceOverrideRetired,
  cadencePanelVm,
  type ChoreSession,
  choreFormVm,
  removalRefusal,
  whileEditing,
} from './gardening-cadence-detail.model';

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
 * It owns one chore session the same way: a chore add or edit captures the chores and revision it started from, and
 * Save sends the whole chore list rebuilt from that capture, so a cadence changed since is refused, not merged. A
 * remove captures them when its confirm opens. The chore form, the remove confirm, and the cadence edit are open one
 * at a time. A retire or enable is sent against the revision the cadence now reads at.
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
  private readonly choreService = injectChoreService();
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
    this.endForms();
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

  /** The chore add or edit under way — `null` while none is. */
  private readonly choreSession = signal<ChoreSession | null>(null);
  private readonly choreDraft = signal<ChoreDraft>(EMPTY_CHORE_DRAFT);
  private readonly addTree = form(this.choreDraft, choreAddRules);
  private readonly choreEditTree = form(this.choreDraft, choreEditRules);
  /** The chore removal awaiting confirmation: its key and the chores and revision it captured. */
  private readonly removal = signal<{ readonly key: string; readonly baseline: ChoresBaseline } | null>(null);

  /** The chore form's field tree while a chore add or edit is under way. */
  protected readonly choreForm = computed(() => choreFormVm(this.choreSession(), this.addTree, this.choreEditTree));
  protected readonly removing = computed(() => this.removal()?.key ?? null);

  protected onStartAddChore(): void {
    this.beginChore(null, EMPTY_CHORE_DRAFT);
  }

  protected onStartEditChore(key: string): void {
    const cadence = this.selected();
    const chore = cadence === null ? null : baselineChore(toChoresBaseline(cadence), key);
    if (chore !== null) this.beginChore(key, toChoreDraft(chore));
  }

  protected async onSaveChore(): Promise<void> {
    const session = this.choreSession();
    if (session === null) return;
    const { baseline, key } = session;
    const saved = await submitForm(key === null ? this.addTree : this.choreEditTree, (draft) =>
      key === null ? this.choreService.add(baseline, draft) : this.choreService.edit(baseline, key, draft),
    );
    if (saved && this.choreSession() === session) this.choreSession.set(null);
  }

  protected onCancelChore(): void {
    this.choreSession.set(null);
  }

  protected onStartRemoveChore(key: string): void {
    const cadence = this.selected();
    if (cadence === null) return;
    this.endForms();
    this.actionError.set(null);
    this.removal.set({ key, baseline: toChoresBaseline(cadence) });
  }

  protected async onConfirmRemoveChore(): Promise<void> {
    const removal = this.removal();
    if (removal === null) return;
    this.removal.set(null);
    const outcome = await this.choreService.remove(removal.baseline, removal.key);
    if (!outcome.ok) this.actionError.set(removalRefusal(outcome, 'Remove chore failed.'));
  }

  protected onCancelRemoveChore(): void {
    this.removal.set(null);
  }

  private beginChore(key: string | null, draft: ChoreDraft): void {
    const cadence = this.selected();
    if (cadence === null) return;
    this.endForms();
    this.actionError.set(null);
    (key === null ? this.addTree : this.choreEditTree)().reset({ ...draft });
    this.choreSession.set({ baseline: toChoresBaseline(cadence), key });
  }

  /** Ends every form and confirm under way, so one is open at a time. */
  private endForms(): void {
    this.editBaseline.set(null);
    this.choreSession.set(null);
    this.removal.set(null);
  }

  protected onRetire(): void {
    this.lifecycle(true, 'Retire failed.');
  }

  protected onEnable(): void {
    this.lifecycle(false, 'Enable failed.');
  }

  constructor() {
    // Selecting another cadence ends every form.
    effect(() => {
      this.cadenceSlug();
      untracked(() => this.endForms());
    });
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
