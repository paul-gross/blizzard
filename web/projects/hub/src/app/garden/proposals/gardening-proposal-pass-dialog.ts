import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';
import { createRefusalState, submitForm } from 'fleet';

import { emptyPassDraft, type ProposalPassDraft } from './proposal-pass.domain';
import { proposalPassRules } from './proposal-pass.form';
import { injectProposalPassService } from './proposal-pass.service';
import { GardeningProposalPassDialogView } from './gardening-proposal-pass-dialog-view';

/**
 * The garden proposal docket's Pass dialog container —
 * `blizzard hub garden-proposal pass <id> --reason <text>`'s own UI: a single
 * required-reason field. It owns the draft and its `form()`, and answers the view's
 * `save` with `submitForm` over the pass service. Closure is terminal, so a 409 (a
 * raced second close) is a real outcome: it lands on the form line and keeps the
 * dialog open with the draft intact, and is never retried.
 */
@Component({
  selector: 'app-gardening-proposal-pass-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GardeningProposalPassDialogView],
  templateUrl: './gardening-proposal-pass-dialog.html',
})
export class GardeningProposalPassDialog {
  readonly proposalId = input.required<string>();
  readonly proposalTitle = input.required<string>();

  readonly closed = output<void>();

  private readonly service = injectProposalPassService();

  private readonly draft = signal(emptyPassDraft());

  private readonly refusal = createRefusalState<ProposalPassDraft>();

  protected readonly passForm = form(this.draft, (path) => {
    apply(path, proposalPassRules);
    apply(path, this.refusal.schema);
  });

  protected async onSave(): Promise<void> {
    const ok = await submitForm(this.passForm, (draft) => this.service.pass(this.proposalId(), draft), {
      refusalState: this.refusal,
    });
    if (ok) this.closed.emit();
  }
}
