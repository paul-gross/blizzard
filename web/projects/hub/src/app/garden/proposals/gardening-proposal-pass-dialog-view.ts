import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import { KitDialog, KitField, KitForm, KitTextInput } from 'fleet';

import { type ProposalPassDraft } from './proposal-pass.domain';

/**
 * The Pass dialog's presentational view — a `KitForm` over the container's `form()`:
 * one required reason field and nothing else. No query or client dependency
 * (`bzh:frontend-container-presentational`): the container owns the draft and answers
 * {@link save}.
 */
@Component({
  selector: 'app-gardening-proposal-pass-dialog-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitDialog, KitField, KitForm, KitTextInput],
  templateUrl: './gardening-proposal-pass-dialog-view.html',
  styleUrl: './gardening-proposal-pass-dialog-view.css',
})
export class GardeningProposalPassDialogView {
  readonly proposalTitle = input.required<string>();
  readonly form = input.required<FieldTree<ProposalPassDraft>>();

  readonly closed = output<void>();
  readonly save = output<void>();

  /** Escape, a backdrop click, and Cancel all route through one close — gated here so a pass in flight cannot be
   * torn down before it lands. */
  protected onClosed(): void {
    if (this.form()().submitting()) return;
    this.closed.emit();
  }
}
