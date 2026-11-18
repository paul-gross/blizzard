import { Component, input, signal } from '@angular/core';
import { form } from '@angular/forms/signals';

import { gardenCadence } from '../core/cadence.builders';
import type { CadenceDraft } from '../core/cadence.domain';
import { toDraft } from '../core/cadence.translate';
import { cadenceEditRules } from './cadence.form';
import { type CadencePanelVm, FleetCadencePanel } from './cadence-panel';

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
