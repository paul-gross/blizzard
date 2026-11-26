import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type FieldTree, FormField } from '@angular/forms/signals';
import {
  KitDialog,
  KitField,
  KitForm,
  KitOption,
  KitOptionGroup,
  KitSelect,
  KitTextInput,
  type KitChipOption,
} from 'fleet';

import type { ProposalAcceptanceDraft } from './proposal-acceptance.domain';

/**
 * The Accept dialog's presentational view — a `KitForm` over the container's `form()`: the mint/decline choice, the
 * mint's urgency and prefilled work-item body, and one reason field in both modes. The schema hides the mint fields
 * under a decline. No query or client dependency (`bzh:frontend-container-presentational`): the container owns the
 * draft and answers {@link save}.
 *
 * Copy states that acceptance neither promotes the minted item nor changes any finding's state — the surface's own
 * answer to the two things acceptance does not do.
 */
@Component({
  selector: 'app-gardening-proposal-accept-dialog-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormField, KitDialog, KitField, KitForm, KitOption, KitOptionGroup, KitSelect, KitTextInput],
  templateUrl: './gardening-proposal-accept-dialog-view.html',
  styleUrl: './gardening-proposal-accept-dialog-view.css',
})
export class GardeningProposalAcceptDialogView {
  readonly proposalTitle = input.required<string>();
  readonly form = input.required<FieldTree<ProposalAcceptanceDraft>>();
  readonly urgencyOptions = input<readonly KitChipOption[]>([]);

  readonly closed = output<void>();
  readonly save = output<void>();

  /** Escape, a backdrop click, and Cancel all route through one close — gated here so an accept in flight cannot be
   * torn down before it lands. */
  protected onClosed(): void {
    if (this.form()().submitting()) return;
    this.closed.emit();
  }
}
