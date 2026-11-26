// exemplar: bzh:frontend-edit-baseline, bzh:frontend-mutation-settles-on-refresh.

import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { form } from '@angular/forms/signals';

import { type EditBaseline, submitForm } from 'fleet/forms';
import { KitButton } from 'fleet/kit';
import { injectRouteParam } from '../core/route-state';
import { CadenceEditForm } from './cadence-form';
import { cadenceBySlug, whileEditing } from './cadence-edit.model';
import { type CadenceDraft, EMPTY_CADENCE_DRAFT } from './cadence.domain';
import { cadenceEditRules } from './cadence.form';
import { injectCadenceService } from './cadence.service';
import { toEditBaseline } from './cadence.translate';
import { injectCadencesQuery } from './cadences.query';

/** `/cadences/:cadenceSlug` — one cadence's edit. Edit captures the baseline, and Save sends the working draft against
 * it, so a refresh of the read changes neither; a refused save keeps both, and Cancel ends the edit. */
@Component({
  selector: 'app-cadence-edit',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CadenceEditForm, KitButton],
  templateUrl: './cadence-edit.html',
})
export class CadenceEdit {
  private readonly slug = injectRouteParam('cadenceSlug');
  private readonly cadencesQuery = injectCadencesQuery();
  private readonly service = injectCadenceService();

  protected readonly selected = computed(() => cadenceBySlug(this.slug(), this.cadencesQuery.data() ?? []));

  private readonly baseline = signal<EditBaseline<CadenceDraft> | null>(null);
  private readonly draft = signal<CadenceDraft>(EMPTY_CADENCE_DRAFT);
  private readonly tree = form(this.draft, cadenceEditRules);

  protected readonly editForm = computed(() => whileEditing(this.baseline(), this.tree));

  protected onStartEdit(): void {
    const cadence = this.selected();
    if (cadence === null) return;
    const baseline = toEditBaseline(cadence);
    this.tree().reset({ ...baseline.draft });
    this.baseline.set(baseline);
  }

  protected async onSave(): Promise<void> {
    const baseline = this.baseline();
    if (baseline === null) return;
    const saved = await submitForm(this.tree, (draft) => this.service.edit(baseline, draft));
    if (saved && this.baseline() === baseline) this.baseline.set(null);
  }

  protected onCancel(): void {
    this.baseline.set(null);
  }
}
