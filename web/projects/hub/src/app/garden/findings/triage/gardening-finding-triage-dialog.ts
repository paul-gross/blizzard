import { ChangeDetectionStrategy, Component, input, linkedSignal, output } from '@angular/core';
import { form } from '@angular/forms/signals';
import { submitForm } from 'fleet/forms';

import { type FindingTriageVerb } from '../../core/finding-triage-verb.domain';
import { emptyTriageDraft, type FindingTriageDraft } from './finding-triage.domain';
import { findingTriageRules } from './finding-triage.form';
import { injectFindingTriageService } from './finding-triage.service';
import { GardeningFindingTriageDialogView } from './gardening-finding-triage-dialog-view';

/**
 * The findings triage dialog container — one dialog for every verb {@link FindingTriageVerb} names. It owns the
 * draft and its `form()`, and answers the view's `save` with `submitForm` over the triage service. A refusal lands
 * on its field or the form line and keeps the dialog open with the draft intact.
 */
@Component({
  selector: 'app-gardening-finding-triage-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GardeningFindingTriageDialogView],
  templateUrl: './gardening-finding-triage-dialog.html',
})
export class GardeningFindingTriageDialog {
  readonly verb = input.required<FindingTriageVerb>();
  readonly findingIds = input.required<readonly string[]>();

  readonly closed = output<void>();

  /** Emitted once the batch actually lands — distinct from {@link closed}, which
   * also fires on a plain cancel. */
  readonly succeeded = output<void>();

  private readonly service = injectFindingTriageService();

  /** The draft, seeded from the verb the dialog opened under — the verb input is only readable after construction,
   * so the draft is derived from it on first read. */
  private readonly draft = linkedSignal<FindingTriageDraft>(() => emptyTriageDraft(this.verb()));

  protected readonly triageForm = form(this.draft, findingTriageRules);

  protected async onSave(): Promise<void> {
    const ok = await submitForm(this.triageForm, (draft) => this.service.triage(this.findingIds(), draft));
    if (!ok) return;
    this.succeeded.emit();
    this.closed.emit();
  }
}
