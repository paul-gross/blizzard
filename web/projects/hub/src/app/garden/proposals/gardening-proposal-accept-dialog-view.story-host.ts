import { Component, input, signal } from '@angular/core';
import { form, submit } from '@angular/forms/signals';
import type { KitChipOption } from 'fleet/kit/chips';

import { GardeningProposalAcceptDialogView } from './gardening-proposal-accept-dialog-view';
import { acceptanceDraftFor, type ProposalAcceptanceDraft } from './proposal-acceptance.domain';
import { proposalAcceptanceRules } from './proposal-acceptance.form';

/**
 * The Accept dialog view over a `form()` this host owns, as its container owns one. `draft` seeds the fields;
 * `formError` and `reasonError` land a hub refusal on the form line and on the reason; `submitting` holds a save in
 * flight.
 */
@Component({
  selector: 'app-story-proposal-accept-dialog',
  imports: [GardeningProposalAcceptDialogView],
  templateUrl: './gardening-proposal-accept-dialog-view.form-story.html',
})
export class ProposalAcceptDialogStoryHost {
  readonly draft = input<Partial<ProposalAcceptanceDraft>>({});
  readonly urgencyOptions = input<readonly KitChipOption[]>([]);
  readonly formError = input<string | null>(null);
  readonly reasonError = input<string | null>(null);
  readonly submitting = input(false);

  private readonly model = signal<ProposalAcceptanceDraft>(acceptanceDraftFor(''));
  protected readonly acceptForm = form(this.model, proposalAcceptanceRules);

  constructor() {
    queueMicrotask(() => {
      this.model.set({ ...acceptanceDraftFor('Describe the proposed work.'), ...this.draft() });
      const reasonError = this.reasonError();
      const formError = this.formError();
      if (this.submitting()) {
        void submit(this.acceptForm, () => new Promise<undefined>(() => undefined));
      } else if (reasonError !== null || formError !== null) {
        void submit(this.acceptForm, async () => [
          ...(reasonError === null ? [] : [{ kind: 'hub', fieldTree: this.acceptForm.reason, message: reasonError }]),
          ...(formError === null ? [] : [{ kind: 'hub', fieldTree: this.acceptForm, message: formError }]),
        ]);
      }
    });
  }
}
