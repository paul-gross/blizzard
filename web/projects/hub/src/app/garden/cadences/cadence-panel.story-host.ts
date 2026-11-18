import { Component, computed, input, signal } from '@angular/core';
import { form } from '@angular/forms/signals';

import { gardenCadence, gardenChore } from '../core/cadence.builders';
import { type CadenceDraft, type ChoreDraft, EMPTY_CHORE_DRAFT } from '../core/cadence.domain';
import { toChoreDraft, toDraft } from '../core/cadence.translate';
import { cadenceEditRules } from './cadence.form';
import { type CadencePanelVm, type ChoreFormVm, FleetCadencePanel } from './cadence-panel';
import { choreAddRules, choreEditRules } from './chore.form';

/** The panel mid-edit, over an edit `form()` this host owns as the detail container does. */
@Component({
  selector: 'app-story-cadence-panel-editing',
  imports: [FleetCadencePanel],
  templateUrl: './cadence-panel.form-story.html',
})
export class CadencePanelEditingStoryHost {
  readonly vm = input.required<CadencePanelVm | null>();
  protected readonly tree = form(signal<CadenceDraft>(toDraft(gardenCadence({ slug: 'evening', name: 'Evening sweep' }))), cadenceEditRules);
}

/** The panel with a chore form open — adding, or editing the first chore — over a `form()` this host owns. */
@Component({
  selector: 'app-story-cadence-panel-chore',
  imports: [FleetCadencePanel],
  templateUrl: './cadence-panel.chore-story.html',
})
export class CadencePanelChoreStoryHost {
  readonly vm = input.required<CadencePanelVm | null>();
  readonly adding = input(true);
  private readonly model = signal<ChoreDraft>(EMPTY_CHORE_DRAFT);
  private readonly addTree = form(this.model, choreAddRules);
  private readonly editTree = form(this.model, choreEditRules);

  protected readonly choreForm = computed<ChoreFormVm>(() =>
    this.adding() ? { key: null, form: this.addTree } : { key: this.editKey(), form: this.editTree },
  );
  private readonly editKey = computed(() => this.vm()?.chores[0]?.key ?? 'nightly');

  constructor() {
    queueMicrotask(() => {
      if (!this.adding()) this.model.set(toChoreDraft(gardenChore({ key: this.editKey() })));
    });
  }
}
